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
from urllib.parse import urlparse

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


class BotCheckError(KatalogError):
    """The catalogue kept answering with its anti-bot check."""


# Requests without an ``AuthToken`` cookie (e.g. searches) are redirected to
# /library/<lib>/i-am-not-a-robot. That page only runs a tiny script: it
# fetches /jsapi/pow/token ({"token": "<uuid>"}), stores it in the
# ``AuthToken`` cookie and reloads. Despite the name there is no proof of
# work to compute. Neknihy does the same once and repeats the request.
BOT_CHECK_MARKERS = ("i-am-not-a-robot", "i-am-a-robot")
TOKEN_PATH = "/jsapi/pow/token"
TOKEN_COOKIE = "AuthToken"


def is_bot_check(response):
    url = getattr(response, "url", "") or ""
    loc = (getattr(response, "headers", None) or {}).get("Location", "") or ""
    return any(m in url or m in loc for m in BOT_CHECK_MARKERS)


BOT_CHECK_MSG = ("The library catalogue keeps asking for its browser check "
                 "(anti-bot page). Try again later or use the catalogue in "
                 "your web browser.")


def pass_bot_check(session, base_url=BASE_URL, timeout=30):
    """Get a token from the catalogue and keep it as the AuthToken cookie."""
    r = session.get(base_url.rstrip("/") + TOKEN_PATH, timeout=timeout)
    r.raise_for_status()
    try:
        token = (r.json() or {}).get("token")
    except ValueError:
        token = None
    if not token:
        raise BotCheckError(BOT_CHECK_MSG)
    session.cookies.set(TOKEN_COOKIE, token, domain=urlparse(base_url).hostname, path="/")
    log.info("Catalogue browser check passed")


def get_checked(session, url, base_url=BASE_URL, timeout=30, **kwargs):
    """GET that passes the anti-bot check once if the catalogue asks for it."""
    r = session.get(url, timeout=timeout, **kwargs)
    if is_bot_check(r):
        pass_bot_check(session, base_url, timeout)
        r = session.get(url, timeout=timeout, **kwargs)
        if is_bot_check(r):
            raise BotCheckError(BOT_CHECK_MSG)
    return r


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
        self.base_url = base_url.rstrip("/")
        self.lib_url = "%s/library/%s" % (self.base_url, self.library)
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
        r = get_checked(self.session, self._url(path), self.base_url, self.timeout)
        r.raise_for_status()
        parser = _CsrfParser()
        parser.feed(r.text)
        if not parser.token:
            raise KatalogError("No _csrf token found on %s" % r.url)
        return parser.token

    def login(self):
        token = self._get_csrf("/")
        # The login form posts to /process-login at the site root (the
        # library is passed in "wd"). /library/<lib>/process-login is not the
        # login handler: it just redirects to the library page, which looks
        # like a successful login but leaves the session anonymous.
        r = self.session.post(
            self.base_url + "/process-login",
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
        log.info("Catalogue login answered %d -> %s", r.status_code, loc or "(no redirect)")
        if is_bot_check(r):
            raise BotCheckError(BOT_CHECK_MSG)
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
        r = get_checked(self.session, self._url(path), self.base_url, self.timeout,
                        params=params, allow_redirects=False)
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

    def borrow(self, work_id, provider_id=-1, retry=True):
        """Borrow an e-book. Returns the confirmation page URL.

        If the catalogue sends us back to its login page, log in again and
        retry once. If it still wants a login right after a successful one,
        the login was not really accepted.
        """
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
        if is_bot_check(r):
            raise BotCheckError(BOT_CHECK_MSG)
        if "login" in loc.lower():
            self.logged_in = False
            if retry:
                log.warning("Catalogue asked for login during loan (%s), logging in again", loc)
                return self.borrow(work_id, provider_id, retry=False)
            raise LoginError("The catalogue keeps asking for login right after logging in "
                             "(%s). Check the catalogue user name/password in Settings and "
                             "try logging in at katalog.tsk.sk in a browser." % loc)
        raise BorrowError("Loan of workId=%s failed: %d -> %s"
                          % (work_id, r.status_code, loc or "(no redirect)"))


# --- search -----------------------------------------------------------------
#
# Searching needs no login. The catalogue renders results as HTML (no JSON
# API for them), one <div class="result" data-work-id="..."> per work.
# Harvested Palmknihy e-books carry a link to palmknihy.cz/kniha/<id>, which
# is the same palm_id Neknihy sees in the Palmknihy loans list.

# Catalogue "area" that holds e-books, per library. Other libraries search
# the whole collection and keep only results that can be e-borrowed.
EBOOK_AREAS = {"vkmr": 701}
AREA_ALL = -1
PAGE_SIZE = 100  # the catalogue accepts this; most searches fit one page

_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
              "meta", "source", "track", "wbr"}
_FIELDS = {"Title": "title", "Authors": "authors", "Publish_info": "publish_info",
           "Keywords": "keywords"}
_TOTAL_RE = re.compile(r"z\s+celkom\s+(\d+)|of\s+(\d+)|z\s+celkem\s+(\d+)", re.IGNORECASE)
_PALM_RE = re.compile(r"palmknihy\.cz/kniha/(\d+)")


def _clean(text):
    return " ".join(text.split())


class _SearchParser(HTMLParser):
    """Turn a catalogue search result page into a list of dicts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self.total = None
        self._stack = []       # open (tag, classes) of the current document
        self._cur = None       # result being parsed
        self._cur_depth = 0    # stack depth of its <div>
        self._field = None     # (name, depth) of the field div we are in
        self._item = None      # (text parts, depth) of the current showitem-content
        self._info_depth = None
        self._info = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag in _VOID_TAGS:
            self._void(tag, a, classes)
            return
        self._stack.append(tag)
        depth = len(self._stack)

        if tag == "div" and "pagination-info" in classes:
            self._info_depth = depth
        if tag == "div" and "result" in classes and a.get("data-work-id"):
            try:
                work_id = int(a["data-work-id"])
            except ValueError:
                return
            isbn = [p.strip(' "[]') for p in (a.get("data-isbn") or "").split(",")]
            self._cur = {
                "work_id": work_id, "title": "", "authors": [], "publish_info": "",
                "keywords": [], "isbn": [p for p in isbn if p], "work_type": "",
                "ebook": False, "source": "", "palm_id": None, "cover": "",
            }
            self._cur_depth = depth
            return
        if self._cur is None:
            return

        r = self._cur
        if tag == "div" and self._field is None:
            for cls, name in _FIELDS.items():
                if cls in classes:
                    self._field = (name, depth)
        if tag == "div" and "work-availability" in classes:
            title = a.get("data-original-title") or a.get("title") or ""
            if ":" in title:
                r["source"] = title.split(":", 1)[1].strip()
        if tag == "span" and "work-type" in classes:
            r["work_type"] = (a.get("data-original-title") or a.get("title") or "") \
                .strip().rstrip("-").strip()
        if tag == "span" and "showitem-content" in classes and self._field \
                and self._item is None:
            self._item = ([], depth)
        if tag == "a":
            href = a.get("href") or ""
            if "/eborrow/" in href:
                r["ebook"] = True
            m = _PALM_RE.search(href)
            if m and r["palm_id"] is None:
                r["palm_id"] = int(m.group(1))
            if a.get("data-large-cover") and not r["cover"]:
                r["cover"] = a["data-large-cover"]

    def _void(self, tag, a, classes):
        if self._cur is not None and tag == "img" and not self._cur["cover"]:
            src = a.get("src") or ""
            if src.startswith("http"):
                self._cur["cover"] = src

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS or tag not in self._stack:
            return
        # close up to the matching tag (tolerates sloppy HTML)
        while self._stack:
            depth = len(self._stack)
            if self._item and depth == self._item[1]:
                self._end_item()
            if self._field and depth == self._field[1]:
                self._field = None
            if self._info_depth == depth:
                self._info_depth = None
                m = _TOTAL_RE.search(_clean(" ".join(self._info)))
                if m:
                    self.total = int(next(g for g in m.groups() if g))
            if self._cur is not None and depth == self._cur_depth:
                self.results.append(self._cur)
                self._cur = None
            if self._stack.pop() == tag:
                break

    def _end_item(self):
        text = _clean(" ".join(self._item[0]))
        self._item = None
        if not text or self._cur is None or self._field is None:
            return
        name = self._field[0]
        r = self._cur
        if name in ("authors", "keywords"):
            if text not in r[name]:
                r[name].append(text)
        elif not r[name]:
            r[name] = text

    def handle_data(self, data):
        if self._info_depth is not None:
            self._info.append(data)
        if self._item is not None:
            self._item[0].append(data)


def parse_search(html):
    """Parse a search result page: {"total": int, "results": [dict, ...]}."""
    p = _SearchParser()
    p.feed(html)
    p.close()
    total = p.total if p.total is not None else len(p.results)
    return {"total": total, "results": p.results}


def search(query, library="vkmr", ebooks_only=True, page=1, page_size=PAGE_SIZE,
           session=None, base_url=BASE_URL, timeout=30):
    """Search the catalogue; no login needed.

    Returns {"query", "page", "pages", "total", "results"}. Each result has
    work_id, title, authors, publish_info, isbn, work_type, ebook (can be
    e-borrowed), source, palm_id (Palmknihy book ID or None), cover, keywords.
    """
    library = library or "vkmr"
    area = EBOOK_AREAS.get(library, AREA_ALL) if ebooks_only else AREA_ALL
    params = {"field": 0, "q": query, "area": area, "allsrc": "on",
              "pageSize": page_size, "page": page}
    own = session is None
    s = session or requests.Session()
    if own:
        s.headers.update({"User-Agent": USER_AGENT})
    try:
        r = get_checked(s, "%s/library/%s/search" % (base_url.rstrip("/"), library),
                        base_url, timeout, params=params)
        r.raise_for_status()
    finally:
        if own:
            s.close()
    data = parse_search(r.text)
    if ebooks_only and area == AREA_ALL:
        data["results"] = [x for x in data["results"] if x["ebook"]]
    data.update(query=query, page=page,
                pages=max(1, -(-data["total"] // page_size)))
    log.info("Catalogue search '%s': %d of %d", query, len(data["results"]), data["total"])
    return data
