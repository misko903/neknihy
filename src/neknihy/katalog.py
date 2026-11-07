"""Client for the TSK online library catalogue (katalog.tsk.sk).

Used by libraries of the Trenčín self-governing region (e.g. VKMR Trenčín)
to lend Palmknihy e-books. After a successful loan the book shows up in the
Palmknihy account and Neknihy downloads it as usual.

The catalogue is a Spring application: login and loans are plain HTML form
posts protected by a ``_csrf`` token bound to the session cookie.

    k = KatalogTSK("me@example.com", "secret", library="vkmr")
    k.borrow(1439847)
"""

import logging
import re
from html.parser import HTMLParser

import requests

log = logging.getLogger("neknihy")

BASE_URL = "https://katalog.tsk.sk"
USER_AGENT = "Neknihy (+https://github.com/misko903/neknihy)"
REDIRECT_CODES = (301, 302, 303, 307, 308)


class KatalogError(RuntimeError):
    """Communication with the catalogue failed."""


class LoginError(KatalogError):
    """Wrong credentials or the login form has changed."""


class BorrowError(KatalogError):
    """The loan was not accepted."""


def parse_work_id(text):
    """Return the work ID from a catalogue link or a plain number, else None.

    Accepts e.g. ``1439847``, ``...?workId=1439847&...`` or
    ``.../eborrow-success/1439847/-1``.
    """
    text = (text or "").strip()
    if text.isdigit():
        return int(text)
    m = re.search(r"[?&]workId=(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"/(\d{4,})(?:[/?#]|$)", text)
    if m:
        return int(m.group(1))
    return None


class _CsrfParser(HTMLParser):
    """Find the Spring Security CSRF token (hidden input or <meta>)."""

    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        if self.token is not None:
            return
        a = dict(attrs)
        if tag == "input" and a.get("name") == "_csrf" and a.get("value"):
            self.token = a["value"]
        elif tag == "meta" and a.get("name") == "_csrf" and a.get("content"):
            self.token = a["content"]


class KatalogTSK():
    def __init__(self, username, password, library="vkmr", base_url=BASE_URL, timeout=30):
        self.username = username
        self.password = password
        self.library = library or "vkmr"
        self.lib_url = "%s/library/%s" % (base_url.rstrip("/"), self.library)
        self.timeout = timeout
        self.logged_in = False
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def _url(self, path):
        return "%s/%s" % (self.lib_url, path.lstrip("/"))

    def _get_csrf(self, path="/"):
        """Load a catalogue page and return its _csrf token.

        The token is bound to the session, so any page of the current
        session will do.
        """
        r = self.session.get(self._url(path), timeout=self.timeout)
        r.raise_for_status()
        parser = _CsrfParser()
        parser.feed(r.text)
        if not parser.token:
            raise KatalogError("No _csrf token found on %s" % r.url)
        return parser.token

    def login(self):
        token = self._get_csrf("/")
        r = self.session.post(
            self._url("process-login"),
            data={
                "wd": self.library,
                "target_url": "",
                "username": self.username,
                "password": self.password,
                "_csrf": token,
            },
            allow_redirects=False,
            timeout=self.timeout,
        )
        loc = r.headers.get("Location", "")
        if r.status_code not in REDIRECT_CODES:
            raise LoginError("Catalogue login: unexpected response %d" % r.status_code)
        if "login" in loc.lower() or "error" in loc.lower():
            raise LoginError("Catalogue login refused, check user name and password")
        # Spring issues a new JSESSIONID after login; the session keeps it.
        self.logged_in = True
        log.info("Logged in to catalogue %s", self.library)

    def logout(self):
        self.logged_in = False
        self.session.cookies.clear()

    def _ensure_login(self):
        if not self.logged_in:
            self.login()

    def _get_json(self, path, params=None, retry=True):
        self._ensure_login()
        r = self.session.get(self._url(path), params=params,
                             allow_redirects=False, timeout=self.timeout)
        if r.status_code in REDIRECT_CODES or r.status_code in (401, 403):
            if retry:  # session expired -> log in again once
                self.logged_in = False
                return self._get_json(path, params, retry=False)
            raise KatalogError("%s: access denied (%d)" % (path, r.status_code))
        r.raise_for_status()
        return r.json()

    def user_data(self):
        """Data of the logged-in reader (structure as returned by the catalogue)."""
        return self._get_json("jsapi/user-data")

    def work(self, work_id, order="MOST_RECOMENDEND"):
        """Work detail. 'RECOMENDEND' is the catalogue's own spelling, keep it."""
        return self._get_json("jsapi/work", params={"workId": work_id, "order": order})

    def borrow(self, work_id, provider_id=-1):
        """Borrow an e-book. Returns the confirmation page URL."""
        self._ensure_login()
        token = self._get_csrf("/")
        r = self.session.post(
            self._url("request-eborrow"),
            data={
                "workId": str(work_id),
                "providerId": str(provider_id),
                "agree": "on",
                "_csrf": token,
            },
            allow_redirects=False,
            timeout=self.timeout,
        )
        loc = r.headers.get("Location", "")
        if r.status_code in REDIRECT_CODES and "eborrow-success" in loc:
            log.info("Borrowed from catalogue: workId=%s", work_id)
            return loc
        if "login" in loc.lower():
            self.logged_in = False
            raise BorrowError("Catalogue session expired, try again")
        raise BorrowError("Loan of workId=%s failed: %d -> %s"
                          % (work_id, r.status_code, loc or "(no redirect)"))
