"""Search in DAWINCI library catalogues (Mestská knižnica v Bratislave).

DAWINCI keeps the search in the server-side session: a form POST runs it,
then ``?fn=resultform`` shows the results, 10 per page; other pages are
AJAX "navigator" events. Searching needs no login.

The catalogue offers "Palmknihy ebooky" / "Palmknihy audiobooky" as search
fields; they only filter (the text in them is ignored), so a search is that
filter AND one real field. AND binds tighter than OR and there are just
three rows, so "filter AND (title OR author)" does not fit in one search:
the title and the author search run side by side and are merged.

The result list has no Palmknihy link. Palmknihy's own pseudo-ISBNs carry
the book ID (9990 + 8 digits + check digit, 9990004167919 -> 416791); other
books get it from their detail page, fetched in parallel and cached.
"""
import html
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

log = logging.getLogger("neknihy")

USER_AGENT = "Neknihy-NextGen (+https://github.com/misko903/neknihy)"

# library code -> OPAC URL
LIBRARIES = {
    "mkb": ("Mestská knižnica v Bratislave", "https://opac.mestskakniznica.sk/opac"),
}

# "condCombo" values of the search form
F_AUTHOR, F_TITLE = "3", "4"
F_PALM = {"ebook": "13", "audiobook": "14"}
PAGE_SIZE = 10        # the catalogue's default; bigger pages render very slowly
MAX_SHOWN = 1000      # the catalogue never lists more records of one search
DETAIL_WORKERS = 4

_COUNT_RE = re.compile(r"z\S*znamov:\s*<b>\s*(\d+)\s*/\s*(\d+)")
_RECORD_RE = re.compile(r'<div id="divChck_\d+"')
_UID_RE = re.compile(r"fn=\*recview&(?:amp;)?uid=(\d+)")
_TITLE_RE = re.compile(r"<b><i>(.*?)</i></b>(.*?)<br", re.S)
_ISBN_RE = re.compile(r"ISBN\s*([0-9Xx-]{10,17})")
_LINK_RE = re.compile(r'href="\?fn=resultform&(?:amp;)?(keyWord|author)=[^"]*">(.*?)</a>', re.S)
_PALM_RE = re.compile(r"palmknihy\.cz/kniha/(\d+)")
# the catalogue's own covers (coverlinker.biblib.net) are all one placeholder
PALM_COVER = "https://core.palmknihy.cz/api/books/%d/cover/400"

_palm_cache = {}      # (base_url, uid) -> Palmknihy ID or None
_cache_lock = threading.Lock()


class DawinciError(RuntimeError):
    """Communication with the DAWINCI catalogue failed."""


def is_library(code):
    return code in LIBRARIES


def base_url(library):
    return LIBRARIES[library][1]


def detail_url(library, uid):
    return "%s?fn=*recview&uid=%s&full=0" % (base_url(library), uid)


def palm_from_isbn(isbn):
    """Palmknihy pseudo-ISBN -> book ID: '9990004167919' -> 416791."""
    digits = re.sub(r"\D", "", isbn or "")
    # 9999… codes (e.g. Supraphon audiobooks) are something else
    if len(digits) == 13 and digits.startswith("9990"):
        return int(digits[3:12]) or None
    return None


def _text(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse_record(block, audiobook=False):
    """One record of the result list -> the same dict katalog.search() gives."""
    m = _UID_RE.search(block)
    if not m:
        return None
    r = {"work_id": int(m.group(1)), "title": "", "authors": [], "publish_info": "",
         "keywords": [], "isbn": [], "work_type": "", "ebook": True,
         "audiobook": audiobook, "source": "Palmknihy", "palm_id": None, "cover": ""}
    m = _TITLE_RE.search(block)
    if m:
        r["title"] = _text(m.group(1))
        # " / Agatha Christie : Kalibr, 2025. - ISBN 9788024296401."
        rest = _text(m.group(2))
        rest = re.split(r"\.\s+-\s+ISBN", rest)[0].strip(" .")
        if " : " in rest:
            rest = rest.split(" : ", 1)[1]
        elif rest.startswith("/"):
            rest = ""
        r["publish_info"] = rest.strip(" .")
    r["isbn"] = [i.replace("-", "") for i in _ISBN_RE.findall(block)]
    for kind, name in _LINK_RE.findall(block):
        name = _text(name)
        key = "authors" if kind == "author" else "keywords"
        if name and name not in r[key]:
            r[key].append(name)
    for isbn in r["isbn"]:
        r["palm_id"] = r["palm_id"] or palm_from_isbn(isbn)
    return r


def parse_results(page_html, audiobook=False):
    """A result page -> {"total": int, "results": [dict, ...]}."""
    m = _COUNT_RE.search(page_html)
    total = int(m.group(2)) if m else 0
    starts = [x.start() for x in _RECORD_RE.finditer(page_html)]
    results = []
    for a, b in zip(starts, starts[1:] + [len(page_html)]):
        r = parse_record(page_html[a:b], audiobook)
        if r is not None:
            results.append(r)
    return {"total": total, "results": results}


def _search_form(field, query, kind):
    return {
        "fn": "searchform", "fo": "anchSearch", "fc": "", "fa": "performSearch", "aa": "",
        "condCombo1": F_PALM[kind], "srchEdit1": "x", "typeCombo1": "0", "boolCombo1": "AND",
        # "2" = all words, in any order
        "condCombo2": field, "srchEdit2": query, "typeCombo2": "2", "boolCombo2": "AND",
        "condCombo3": F_TITLE, "srchEdit3": "", "typeCombo3": "0", "cmblb_Location": "",
    }


def _hidden_inputs(page_html):
    form = {}
    for tag in re.findall(r"<input[^>]*>", page_html):
        name = re.search(r'name="([^"]*)"', tag)
        if name and 'type="hidden"' in tag:
            value = re.search(r'value="([^"]*)"', tag)
            form[name.group(1)] = html.unescape(value.group(1)) if value else ""
    return form


def search_one(url, field, query, kind, page=1, timeout=30):
    """One search (one field, one kind) in its own session; one result page."""
    with requests.Session() as s:
        s.headers.update({"User-Agent": USER_AGENT})
        r = s.post(url + "?fn=searchform", data=_search_form(field, query, kind),
                   timeout=timeout)
        r.raise_for_status()
        if "nothing=true" in r.text:
            return {"total": 0, "results": []}
        r = s.get(url, params={"fn": "resultform", "prequelF": ""}, timeout=timeout)
        r.raise_for_status()
        if "nothing=true" in r.text:
            return {"total": 0, "results": []}
        if not _COUNT_RE.search(r.text):
            raise DawinciError("Unexpected answer from the catalogue (%s)" % r.url)
        first = parse_results(r.text, audiobook=(kind == "audiobook"))
        if page == 1:
            return first
        if page > _pages(first["total"]):
            return {"total": first["total"], "results": []}
        form = _hidden_inputs(r.text)
        form.update(fo="DWNavigator1", aa="navigator:DWNavigator1_%d" % page, dwcall="ajax")
        s.post(url + "?fn=resultform", data=form, timeout=timeout).raise_for_status()
        r = s.get(url, params={"fn": "resultform"}, timeout=timeout)
        r.raise_for_status()
        return parse_results(r.text, audiobook=(kind == "audiobook"))


def _pages(total):
    return max(1, -(-min(total, MAX_SHOWN) // PAGE_SIZE))


def _palm_id(url, uid, timeout):
    key = (url, uid)
    with _cache_lock:
        if key in _palm_cache:
            return _palm_cache[key]
    try:
        r = requests.get(url, params={"fn": "*recview", "uid": uid, "full": "0"},
                         headers={"User-Agent": USER_AGENT}, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        log.info("Catalogue record %s: %s", uid, e)
        return None   # not cached: tried again next time
    m = _PALM_RE.search(r.text)
    palm = int(m.group(1)) if m else None
    with _cache_lock:
        _palm_cache[key] = palm
    return palm


def search(query, library="mkb", kind="ebook", page=1, timeout=30):
    """Search e-books / audiobooks by title and by author.

    Returns the same shape as katalog.search(); results also carry
    ``detail_url``.
    """
    url = base_url(library)
    kinds = ("ebook", "audiobook") if kind == "all" else (kind,)
    jobs = [(field, k) for k in kinds for field in (F_TITLE, F_AUTHOR)]
    with ThreadPoolExecutor(len(jobs)) as ex:
        parts = list(ex.map(lambda j: search_one(url, j[0], query, j[1], page, timeout),
                            jobs))
    seen = set()
    results = []
    for part in parts:
        for r in part["results"]:
            if r["work_id"] not in seen:
                seen.add(r["work_id"])
                r["detail_url"] = detail_url(library, r["work_id"])
                results.append(r)
    missing = [r for r in results if r["palm_id"] is None]
    if missing:
        with ThreadPoolExecutor(DETAIL_WORKERS) as ex:
            ids = ex.map(lambda r: _palm_id(url, r["work_id"], timeout), missing)
            for r, palm in zip(missing, list(ids)):
                r["palm_id"] = palm
    for r in results:
        if r["palm_id"]:
            r["cover"] = PALM_COVER % r["palm_id"]
    pages = max(_pages(p["total"]) for p in parts)
    # title and author hits overlap: the sum is an upper bound
    total = len(results) if pages == 1 else sum(p["total"] for p in parts)
    log.info("Catalogue search '%s' (%s): %d of %d", query, library, len(results), total)
    return {"total": total, "results": results, "query": query, "kind": kind,
            "page": page, "pages": pages}
