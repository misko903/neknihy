"""HTML rendering of the Neknihy NextGen web interface.

The pages are built on the server as plain strings; styles and scripts are
the static files app.css and app.js next to the icons.
"""

import hashlib
import html
import logging
import os
from datetime import datetime
from urllib.parse import quote

from neknihy import i18n
from neknihy import katalog
from neknihy.api import catalogueIds, isAudiobook, webId
from neknihy_cli import env

log = logging.getLogger("neknihy")

STATUSES = ("new", "pending", "ok", "expired")
WAIT_STEP = int(env("NEKNIHY_WAIT_STEP", "20"))  # s between checks after a loan
# Where the Audiobookshelf library is usually mounted in the container; used
# as the audiobook folder on first start if it exists.
AUDIOBOOKSHELF_DIRS = ("/audiobooks",)
PAGE_REFRESH = 10  # s, auto-reload of the loans page while something is going on

# Inline SVG icons (24x24, stroke = currentColor, so they follow the theme
# and the white text of primary buttons).
_ICON_PATHS = {
    "run": "<polygon points='6 4 20 12 6 20 6 4'/>",
    "refresh": "<path d='M21 12a9 9 0 1 1-2.64-6.36L21 8'/><path d='M21 3v5h-5'/>",
    "download": "<path d='M12 3v12'/><path d='m7 10 5 5 5-5'/><path d='M5 21h14'/>",
    "sync": "<rect x='5' y='2' width='14' height='20' rx='2'/><path d='M9 10l3 3 3-3'/>"
            "<path d='M12 6v7'/><path d='M10 18h4'/>",
    "return": "<path d='M9 14 4 9l5-5'/><path d='M4 9h10.5a5.5 5.5 0 0 1 0 11H11'/>",
    "delete": "<path d='M4 7h16'/><path d='M10 11v6'/><path d='M14 11v6'/>"
              "<path d='M6 7l1 13h10l1-13'/><path d='M9 7V4h6v3'/>",
    "borrow": "<path d='M12 5v14'/><path d='M5 12h14'/>",
    "search": "<circle cx='11' cy='11' r='7'/><path d='m20 20-3.5-3.5'/>",
    "filter": "<path d='M3 5h18l-7 8.5V19l-4 2v-7.5z'/>",
    "more": "<circle cx='12' cy='5' r='1'/><circle cx='12' cy='12' r='1'/>"
            "<circle cx='12' cy='19' r='1'/>",
    "loans": "<path d='M2 4h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2z'/>"
             "<path d='M22 4h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z'/>",
    "settings": "<path d='M4 6h10'/><path d='M18 6h2'/><circle cx='16' cy='6' r='2'/>"
                "<path d='M4 12h4'/><path d='M12 12h8'/><circle cx='10' cy='12' r='2'/>"
                "<path d='M4 18h12'/><path d='M20 18h0'/><circle cx='18' cy='18' r='2'/>",
    "lang": "<circle cx='12' cy='12' r='9'/><path d='M3 12h18'/>"
            "<path d='M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18'/>",
    "new": "<path d='M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z'/>",
    "pending": "<circle cx='12' cy='12' r='9'/><path d='M12 7v5l3 2'/>",
    "ok": "<circle cx='12' cy='12' r='9'/><path d='m8 12 3 3 5-6'/>",
    "audio": "<path d='M4 14v-2a8 8 0 0 1 16 0v2'/><rect x='3' y='14' width='4' height='6' rx='1'/>"
             "<rect x='17' y='14' width='4' height='6' rx='1'/>",
    "ebook": "<path d='M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z'/>"
             "<path d='M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5'/>",
    "both": "<path d='M2 18V6a2 2 0 0 1 2-2h5v16H4a2 2 0 0 1-2-2z'/><path d='M5 4v16'/>"
            "<path d='M12 15v-2a5 5 0 0 1 10 0v2'/>"
            "<rect x='11' y='15' width='3' height='5' rx='1'/>"
            "<rect x='20' y='15' width='3' height='5' rx='1'/>",
    "sun": "<circle cx='12' cy='12' r='4'/><path d='M12 2v2'/><path d='M12 20v2'/>"
           "<path d='m4.9 4.9 1.4 1.4'/><path d='m17.7 17.7 1.4 1.4'/><path d='M2 12h2'/>"
           "<path d='M20 12h2'/><path d='m4.9 19.1 1.4-1.4'/><path d='m17.7 6.3 1.4-1.4'/>",
    "auto": "<circle cx='12' cy='12' r='9'/><path d='M12 3a9 9 0 0 1 0 18z' fill='currentColor'/>",
    "moon": "<path d='M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z'/>",
    "expired": "<circle cx='12' cy='12' r='9'/><path d='m9 9 6 6'/><path d='m15 9-6 6'/>",
    "info": "<circle cx='12' cy='12' r='9'/><path d='M12 11v5'/><path d='M12 8h0'/>",
    "warn": "<path d='M12 3 2 20h20z'/><path d='M12 10v4'/><path d='M12 17h0'/>",
    "log": "<path d='M4 6h16'/><path d='M4 12h16'/><path d='M4 18h10'/>",
    "v-compact": "<path d='M8 6h13'/><path d='M8 12h13'/><path d='M8 18h13'/>"
                 "<path d='M3 6h0'/><path d='M3 12h0'/><path d='M3 18h0'/>",
    "v-details": "<rect x='3' y='4' width='6' height='6' rx='1'/>"
                 "<rect x='3' y='14' width='6' height='6' rx='1'/><path d='M13 6h8'/>"
                 "<path d='M13 9h5'/><path d='M13 16h8'/><path d='M13 19h5'/>",
    "v-grid": "<rect x='3' y='3' width='7' height='7' rx='1'/>"
              "<rect x='14' y='3' width='7' height='7' rx='1'/>"
              "<rect x='3' y='14' width='7' height='7' rx='1'/>"
              "<rect x='14' y='14' width='7' height='7' rx='1'/>",
    "empty": "<path d='M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z'/>"
             "<path d='M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5'/>",
}


def icon(name, cls="ic"):
    return ("<svg class='%s' viewBox='0 0 24 24' fill='none' stroke='currentColor' "
            "stroke-width='2' stroke-linecap='round' stroke-linejoin='round' "
            "aria-hidden='true'>%s</svg>" % (cls, _ICON_PATHS.get(name, "")))


STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(i18n.__file__)), "static")
STATIC = {"/favicon.ico": "icon-32.png", "/favicon.png": "icon-32.png",
          "/icon.png": "icon-64.png", "/apple-touch-icon.png": "icon-180.png",
          "/app.css": "app.css", "/app.js": "app.js"}


def _static_version():
    """Short hash of the static files (icons, styles, scripts). It goes into
    their URLs, so a browser fetches a changed file at once instead of keeping
    its cached copy."""
    digest = hashlib.sha1()
    for name in sorted(set(STATIC.values())):
        try:
            with open(os.path.join(STATIC_DIR, name), "rb") as f:
                digest.update(f.read())
        except OSError:
            pass
    return digest.hexdigest()[:8]


STATIC_VER = _static_version()


def esc(v):
    return html.escape(str(v if v is not None else ""))


KIND_ICONS = {"ebook": "ebook", "audiobook": "audio", "all": "both"}


def header_search(t, query="", kind="ebook"):
    """Catalogue search box in the header: the e-book / audiobook / both
    switch sits at its start (and searches again when there is a query), the
    magnifier button at its end searches, as does Enter."""
    options = "".join(
        "<input type='radio' name='kind' value='%s' id='kind-%s'%s>"
        "<label for='kind-%s' title='%s' aria-label='%s'>%s</label>" % (
            k, k, " checked" if k == kind else "", k, esc(t("kind." + k)),
            esc(t("kind." + k)), icon(KIND_ICONS[k])) for k in katalog.KINDS)
    return ("<form method='get' action='/search' class='hsearch' role='search' "
            "onchange='if(this.q.value.trim())this.submit()'>"
            "<span class='withic'><input type='search' name='q' value='%s' required "
            "placeholder='%s' aria-label='%s'>"
            "<span class='seg' role='radiogroup' aria-label='%s'>%s</span>"
            "<button class='go' title='%s' aria-label='%s'>%s</button></span></form>" % (
                esc(query), esc(t("search.placeholder")), esc(t("btn.search")),
                esc(t("kind.label")), options, esc(t("btn.search")),
                esc(t("btn.search")), icon("search")))


def header_menu(svc, t):
    """⋮ menu next to the search: borrowing by a catalogue link or workId."""
    s = svc.app.settings if svc else None
    if s is not None and not katalog.can_borrow(s.tsk_library):
        inner = "<p class='mut'>%s</p>" % esc(t("borrow.unsupported"))
    elif s is None or s.katalog_configured():
        inner = ("<form method='post' action='/borrow'><label for='work'>%s</label>"
                 "<div class='actions'><input type='text' name='work' id='work' required "
                 "style='flex:1;min-width:0' placeholder='https://katalog.tsk.sk/…?workId=1439847'>"
                 "<button class='primary'>%s<span>%s</span></button></div></form>" % (
                     esc(t("borrow.label")), icon("borrow"), esc(t("btn.borrow"))))
    else:
        inner = not_configured_hint(t)
    return ("<details class='menu'><summary title='%s' aria-label='%s'>%s</summary>"
            "<div class='pop'>%s</div></details>" % (
                esc(t("nav.more")), esc(t("nav.more")), icon("more"), inner))


# The header title always leads to the loans (the home screen). The header
# tab names where it switches to: on the loans page it is "Found" and opens
# the last search (localStorage "neknihy.lastsearch", saved by the search
# page; the tab stays hidden when there is none); elsewhere it is "Loans".
# A click outside the ⋮ menu closes it.


def nav_tabs(t, path):
    """The header tab names where it switches to: the found titles from the
    loans page (shown by app.js once there is a last search), the loans
    from anywhere else."""
    if path == "/":
        return "<a href='/search' id='found' hidden>%s%s</a>" % (
            icon("search"), esc(t("nav.found")))
    return "<a href='/'>%s%s</a>" % (icon("loans"), esc(t("nav.loans")))


def icon_link(t, href, ic, key, path):
    """Icon-only header link (log, settings)."""
    on = href == path
    return "<a href='%s' class='icbtn%s' title='%s' aria-label='%s'%s>%s</a>" % (
        href, " on" if on else "", esc(t(key)), esc(t(key)),
        " aria-current='page'" if on else "", icon(ic))


def page(t, title, body, path="/", refresh=None, query="", kind="ebook", svc=None):
    meta = "<meta http-equiv='refresh' content='%d'>" % refresh if refresh else ""
    return ("<!doctype html><html lang='%s'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>%s"
            "<title>%s</title><link rel='icon' type='image/png' href='/favicon.png?v=%s'>"
            "<link rel='apple-touch-icon' href='/apple-touch-icon.png?v=%s'>"
            "<link rel='stylesheet' href='/app.css?v=%s'><script>%s</script></head><body>"
            "<header><h1><a href='/' id='brand' title='%s'><img src='/icon.png?v=%s' alt=''>"
            "Neknihy NextGen</a></h1>%s%s<nav>%s</nav>"
            "%s%s"
            "<button type='button' class='theme' onclick='nkTheme()' %s>%s%s%s</button>"
            "</header><main>%s</main><script src='/app.js?v=%s'></script></body></html>"
            % (t.lang, meta, esc(title), STATIC_VER, STATIC_VER, STATIC_VER, THEME_JS,
               esc(t("nav.brand")), STATIC_VER, header_search(t, query, kind),
               header_menu(svc, t), nav_tabs(t, path),
               icon_link(t, "/log", "log", "log.title", path),
               icon_link(t, "/settings", "settings", "nav.settings", path),
               " ".join(
                   "data-%s='%s: %s'" % (m, esc(t("theme.toggle")), esc(t("theme." + m)))
                   for m in THEMES),
               icon("auto", "ic t-auto"), icon("sun", "ic t-light"), icon("moon", "ic t-dark"),
               body, STATIC_VER))


# Theme cycle on the header button; "auto" follows the system setting.
THEMES = ("auto", "light", "dark")


# Applied in <head> so the saved theme is set before the first paint; the
# choice is per browser (localStorage "nk-theme"; the old "theme" key, saved
# by the first light/dark-only toggle, is ignored), "auto" (no data-theme) is
# the default.
# The button shows the current mode (CSS) and names it in its title.
THEME_JS = ("(function(){try{var v=localStorage.getItem('nk-theme');"
            "if(v=='light'||v=='dark')document.documentElement.setAttribute('data-theme',v)}"
            "catch(e){}})();")


def fmt_date(t, value):
    try:
        return datetime.fromisoformat(value).strftime(t("fmt.date"))
    except Exception:
        return esc(value)


def sort_th(label, kind="text", cls="", first="asc"):
    """Header of a sortable column (kind "text" or "num"; see app.js)."""
    return ("<th class='sort%s' data-type='%s' data-first='%s'><button type='button'>%s"
            "</button></th>" % (" " + cls if cls else "", kind, first, esc(label)))


def kind_cell(t, audio):
    """Cell saying whether a title is an e-book or an audiobook."""
    label = t("kind.audio_tag" if audio else "kind.ebook_tag")
    return ("<td data-v='%s' title='%s' aria-label='%s'><span class='kind%s'>%s</span></td>"
            % (esc(label), esc(label), esc(label), " audio" if audio else "",
               icon("audio" if audio else "ebook")))


# Ways to show a table (see the table[data-view] CSS and app.js)
VIEWS = ("compact", "details", "grid")


def filter_box(t, table_id):
    """Text box that hides the rows of a table that do not match, and the
    choice of view (see app.js)."""
    views = "".join(
        "<input type='radio' name='view-%s' value='%s' id='view-%s-%s'%s>"
        "<label for='view-%s-%s' title='%s' aria-label='%s'>%s</label>" % (
            table_id, v, table_id, v, " checked" if v == "details" else "", table_id, v,
            esc(t("view." + v)), esc(t("view." + v)), icon("v-" + v)) for v in VIEWS)
    return ("<div class='actions tfilter'><span class='withic' title='%s'>%s"
            "<input type='search' data-for='%s' placeholder='%s' aria-label='%s' "
            "autocomplete='off'></span>"
            "<span class='mut' data-count='%s' hidden></span>"
            "<span class='seg' role='radiogroup' aria-label='%s' data-view-for='%s'>%s</span>"
            "</div>" % (
                esc(t("filter.placeholder")), icon("filter"),
                table_id, esc(t("filter.hint")), esc(t("filter.placeholder")),
                esc(t("filter.count", n="{n}", total="{total}")), esc(t("view.label")),
                table_id, views))


def rating_attrs(t):
    """Texts the table script needs to show ratings it loads later."""
    return ("data-fmt='%s' data-none='%s' data-count='%s'" % (
        esc(t("rating.value", p="{p}")), esc(t("rating.none")), esc(t("rating.count", n="{n}"))))


def rating_cell(svc, t, web_id):
    """Reader rating in percent; unknown ones are loaded by app.js."""
    if web_id is None:
        return "<td class='rt mut' data-v='-1'>–</td>"
    r = svc.ratings.get(web_id)
    if r is None:
        return "<td class='rt mut' data-v='-1' data-rid='%d'>…</td>" % web_id
    if r["percent"] is None:
        return "<td class='rt mut' data-v='-1' title='%s'>–</td>" % esc(t("rating.none"))
    return "<td class='rt' data-v='%d' title='%s'><b>%s</b></td>" % (
        r["percent"], esc(t("rating.count", n=r["count"])),
        esc(t("rating.value", p=r["percent"])))


def not_configured_hint(t):
    link = "<a href='/settings'>%s</a>" % esc(t("settings_link"))
    return "<p class='mut'>%s</p>" % esc(
        t("borrow.not_configured", settings="\x00")).replace("\x00", link)


def render_search(svc, t, query, page_no, kind="ebook"):
    app = svc.app
    s = app.settings
    body = []

    def done(html_body):
        return page(t, "%s – Neknihy NextGen" % t("btn.search"), html_body, "/search",
                    query=query, kind=kind, svc=svc)
    try:
        res = katalog.search(query, library=s.tsk_library or "vkmr", page=page_no,
                             kind=kind)
    except Exception as e:
        log.error("Catalogue search failed: %s", e)
        body.append("<div class='card'><p class='st st-pending'>%s%s</p></div>" % (
            icon("warn"), esc(t("search.error", error=e))))
        return done("".join(body))

    active = [b for b in app.books if b.get("neknihy", {}).get("status") != "expired"]
    # the catalogue links to palmknihy.cz/kniha/<id>, which is often not the
    # loan's palm_id (see catalogueIds); audiobooks are matched by title too
    loaned = set().union(*(catalogueIds(b) for b in active))
    loaned_audio = {(b.get("title") or "").strip().lower() for b in active if isAudiobook(b)}
    library = s.tsk_library or "vkmr"
    borrow_here = katalog.can_borrow(library)
    can_borrow = borrow_here and s.katalog_configured()
    waiting = {aw["work_id"] for aw in svc.awaiting}
    rows = []
    for r in res["results"]:
        if r["work_id"] in waiting:
            action = "<span class='st st-pending'>%s%s</span>" % (
                icon("pending"), esc(t("search.borrowed_wait")))
        elif ((r["palm_id"] is not None and r["palm_id"] in loaned) or
              (r.get("audiobook") and r["title"].strip().lower() in loaned_audio)):
            action = "<span class='st st-ok'>%s%s</span>" % (icon("ok"), esc(t("search.loaned")))
        elif r["ebook"] and can_borrow:
            action = ("<form method='post' action='/borrow'>"
                      "<input type='hidden' name='work' value='%d'>"
                      "<input type='hidden' name='palm_id' value='%s'>"
                      "<input type='hidden' name='title' value='%s'>"
                      "<button class='primary' title='%s'>%s<span>%s</span></button></form>"
                      % (r["work_id"],
                         "" if r.get("audiobook") else esc(r["palm_id"]),
                         esc(r["title"]),
                         esc(t("btn.borrow")), icon("borrow"),
                         esc(t("btn.borrow"))))
        else:
            action = ""
        cover = ("<img src='%s' alt='' loading='lazy' class='cover'>" % esc(r["cover"])
                 if r["cover"] else "")
        authors = "; ".join(r["authors"])
        rows.append(
            "<tr><td class='hide-sm c-cover'>%s</td><td class='c-title' data-v='%s'>"
            "<a href='%s' target='_blank' rel='noopener'>%s</a>"
            "<div class='mut pub'>%s</div></td>%s"
            "<td class='hide-sm c-auth' data-v='%s'>%s</td>%s<td class='c-act'>%s</td></tr>" % (
                cover, esc(r["title"]), esc(katalog.detail_url(library, r)),
                esc(r["title"] or r["work_id"]), esc(r["publish_info"]),
                kind_cell(t, r.get("audiobook")),
                esc(authors), esc(authors), rating_cell(svc, t, r["palm_id"]), action))
    if rows:
        table = ("<p class='mut'>%s</p>%s<table class='sortable' id='search' data-view='details' %s>"
                 "<thead><tr><th class='hide-sm c-cover'></th>%s%s%s%s<th></th></tr></thead><tbody>%s</tbody>"
                 "</table>%s" % (
                     esc(t("search.results_" + res["kind"], n=res["total"])),
                     filter_box(t, "search"), rating_attrs(t),
                     sort_th(t("th.book")), sort_th(t("th.type")),
                     sort_th(t("th.author"), cls="hide-sm"),
                     sort_th(t("th.rating"), "num", first="desc"), "".join(rows), ""))
    else:
        table = "<p class='mut empty'>%s%s</p>" % (icon("empty"), esc(t("search.none")))
    if res["pages"] > 1:
        links = []
        for n in range(1, res["pages"] + 1):
            links.append("<b>%d</b>" % n if n == page_no else
                         "<a href='/search?q=%s&amp;kind=%s&amp;page=%d'>%d</a>"
                         % (quote(query), quote(res["kind"]), n, n))
        table += "<p class='actions'>%s</p>" % " ".join(links)
    if not borrow_here:
        table += "<p class='mut'>%s</p>" % esc(t("borrow.unsupported"))
    elif not can_borrow:
        table += not_configured_hint(t)
    body.append("<div class='card'>%s</div>" % table)
    return done("".join(body))


def render_index(svc, t):
    app = svc.app
    rows = []
    for b in app.books:
        st = b.get("neknihy", {}).get("status", "")
        if st == "deleted":
            continue
        label = t("status." + st) if st in STATUSES else st
        src = b.get("cover") or b.get("base_image_url") or ""
        cover = ("<img src='%s' alt='' loading='lazy' class='cover'>" % esc(src)
                 if src.startswith("https://") else "")
        title = b.get("title") or b.get("palm_id") or ""
        authors = app.bookAuthors(b)
        filename = b.get("neknihy", {}).get("filename", "")
        # the title opens the book on palmknihy.cz when its web ID is known
        wid = webId(b)
        name = ("<a href='https://www.palmknihy.cz/kniha/%s' target='_blank' "
                "rel='noopener'>%s</a>" % (esc(str(wid)), esc(title)) if wid else esc(title))
        delete = ("<td class='c-act'><form method='post' action='/delete' class='del' "
                  "data-confirm='%s'><input type='hidden' name='palm_id' value='%s'>"
                  "<input type='hidden' name='audio' value='%d'>"
                  "<button title='%s' aria-label='%s'>%s</button></form></td>" % (
                      esc(t("delete.confirm", title=title)), esc(b.get("palm_id", "")),
                      isAudiobook(b), esc(t("btn.delete")), esc(t("btn.delete")),
                      icon("delete")))
        rows.append(
            "<tr><td class='c-title' data-v='%s'><div class='bk'>%s<div>%s"
            "<div class='mut only-sm'>%s</div></div></div></td>%s"
            "<td class='hide-sm c-auth' data-v='%s'>%s</td>%s"
            "<td class='hide-sm mut c-file' data-v='%s'>%s</td>"
            "<td class='hide-sm c-due' data-v='%s'>%s</td>"
            "<td data-v='%d'><span class='st st-%s'>%s%s</span></td>%s</tr>" % (
                esc(title), cover, name, esc(authors), kind_cell(t, isAudiobook(b)),
                esc(authors), esc(authors), rating_cell(svc, t, wid),
                esc(filename), esc(filename),
                esc(b.get("end_time") or ""), fmt_date(t, b.get("end_time", "")),
                STATUSES.index(st) if st in STATUSES else len(STATUSES), esc(st),
                icon(st) if st in STATUSES else "", esc(label), delete))
    table = ("%s<table class='sortable' id='loans' data-view='details' %s><thead><tr>%s%s%s%s%s%s%s<th></th></tr>"
             "</thead><tbody>%s</tbody></table>%s%s" % (
                 filter_box(t, "loans"), rating_attrs(t), sort_th(t("th.book")),
                 sort_th(t("th.type")), sort_th(t("th.author"), cls="hide-sm"),
                 sort_th(t("th.rating"), "num", first="desc"),
                 sort_th(t("th.file"), cls="hide-sm"),
                 sort_th(t("th.due"), cls="hide-sm"), sort_th(t("th.status")),
                 "".join(rows), "", "")
             if rows else "<p class='mut empty'>%s%s</p>" % (icon("empty"), esc(t("empty"))))
    status = run_status(svc, t)
    buttons = "".join(
        "<button name='a' value='%s'%s title='%s'>%s<span>%s</span></button>"
        % (a, " class='primary'" if a == "run" else "", esc(t("btn." + a)), icon(a),
           esc(t("btn." + a)))
        for a in ("run", "refresh", "download", "sync", "return"))
    awaiting = "".join(
        "<div class='card'><p class='st st-pending' style='margin:0'>%s<b>%s</b></p>"
        "<p class='mut' style='margin:4px 0 0'>%s</p></div>" % (
            icon("pending"),
            esc(t("await.title", title=aw["title"] or "workId %s" % aw["work_id"])),
            esc(t("await." + aw["state"], s=WAIT_STEP)))
        for aw in list(svc.awaiting))
    # the loans with the actions that work on them (search is in the header)
    body = ("%s<div class='card'><div class='cardhead'><h2>%s%s</h2>"
            "<form method='post' action='/action' class='actions'>%s</form></div>"
            "<p class='mut status'>%s</p>%s%s</div>" % (
                awaiting, icon("loans"), esc(t("nav.loans")),
                buttons, "<br>".join(status), table, ""))
    busy = svc.running or svc.awaiting
    return page(t, "Neknihy NextGen", body, "/", refresh=PAGE_REFRESH if busy else None,
                svc=svc)


def run_status(svc, t):
    """Lines about the last/next run (and a hint when not configured)."""
    app = svc.app
    status = []
    if not app.settings.configured():
        link = "<a href='/settings'>%s</a>" % esc(t("settings_link"))
        hint = esc(t("not_configured.hint", settings="\x00")).replace("\x00", link)
        status.append("<span class='st st-pending'>%s<b>%s</b></span> – %s" % (
            icon("warn"), esc(t("not_configured")), hint))
    if svc.running:
        status.append(esc(t("running")))
    if svc.last_run:
        result = t("result.ok") if svc.last_error is None else t(
            "result.error", error=svc.last_error)
        status.append(esc(t("last_run", time=svc.last_run.strftime(t("fmt.datetime")),
                            result=result)))
    if svc.next_run and svc.web["interval"] > 0:
        status.append(esc(t("next_run", time=datetime.fromtimestamp(
            svc.next_run).strftime(t("fmt.datetime")))))
    return status


def render_log(svc, t):
    lines = "\n".join(reversed(svc.log.lines))
    body = ("<div class='card'><h2 title='%s' aria-label='%s'>%s</h2>"
            "<p class='mut status'>%s</p>%s</div>" % (
        esc(t("log.title")), esc(t("log.title")), icon("log"), "<br>".join(run_status(svc, t)),
        "<pre class='logfull'>%s</pre>" % esc(lines) if lines else
        "<p class='mut empty'>%s%s</p>" % (icon("empty"), esc(t("empty")))))
    busy = svc.running or svc.awaiting
    return page(t, "%s – Neknihy NextGen" % t("log.title"), body, "/log",
                refresh=PAGE_REFRESH if busy else None,
                svc=svc)


def language_select(t, choice):
    """<select> with 'auto' (browser) plus every available language."""
    opts = [("auto", t("set.lang_auto"))] + i18n.available()
    return "<select name='lang' id='lang'>%s</select>" % "".join(
        "<option value='%s'%s>%s</option>" % (
            esc(code), " selected" if code == choice else "", esc(name))
        for code, name in opts)


def library_select(choice):
    """<select> of the supported library catalogues; keeps an unknown code."""
    opts = list(katalog.LIBRARIES)
    choice = choice or "vkmr"
    if choice not in dict(opts):
        opts.append((choice, choice))
    return "<select name='tsk_library' id='tsk_library'>%s</select>" % "".join(
        "<option value='%s'%s>%s</option>" % (
            esc(code), " selected" if code == choice else "", esc(name))
        for code, name in opts)


def render_settings(svc, t, lang_choice="auto"):
    s = svc.app.settings

    def section(ico, title, inner):
        return "<div class='card set'><h2>%s%s</h2>%s</div>" % (icon(ico), esc(t(title)), inner)

    def field(key, control, hint=None):
        return "<label>%s%s</label>%s" % (
            esc(t(key)), control,
            "<small class='hint'>%s</small>" % esc(t(hint)) if hint else "")

    def text(name, value, kind="text", extra=""):
        return " <input type='%s' name='%s' value='%s'%s>" % (kind, name, esc(value), extra)

    def password(name, is_set):
        ph = " placeholder='%s'" % esc(t("set.password_keep")) if is_set else ""
        return " <input type='password' name='%s' autocomplete='new-password'%s>" % (name, ph)

    def check(name, checked, key):
        return ("<label class='chk'><input type='checkbox' name='%s'%s>%s</label>"
                % (name, " checked" if checked else "", esc(t(key))))

    general = "<label>%s%s</label>" % (esc(t("nav.language")), language_select(t, lang_choice))
    account = (field("set.email", text("email", s.email, "email", " required"))
               + field("set.password", password("password", s.password)))
    folders = (field("set.workdir", text("workdir", s.workdir))
               + field("set.readerdir", text("readerdir", s.readerdir), "set.readerdir_hint")
               + check("ingest", svc.web.get("ingest"), "set.ingest")
               + field("set.audiodir",
                       text("audiodir", s.audiodir,
                            extra=" placeholder='%s'" % esc(AUDIOBOOKSHELF_DIRS[0])),
                       "set.audiodir_hint"))
    auto = (field("set.interval",
                  text("interval_h", svc.web["interval"] // 3600, "number", " min='0'"),
                  "set.interval_hint")
            + check("auto_return", svc.web["auto_return"], "set.auto_return"))
    convert = (check("convert", s.convert == "1", "set.convert")
               + field("set.convertor", text("convertor", s.convertor)))
    tsk = (field("set.tsk_library", library_select(s.tsk_library))
           + field("set.tsk_username",
                   text("tsk_username", s.tsk_username, extra=" autocomplete='off'"),
                   "set.tsk_username_hint")
           + field("set.password", password("tsk_password", s.tsk_password)))
    body = ("<form method='post' action='/settings'>%s%s%s%s%s%s"
            "<p><button class='primary'>%s%s</button></p></form>") % (
        section("settings", "set.sec_general", general),
        section("download", "set.sec_account", account),
        section("loans", "set.tsk", tsk),
        section("ebook", "set.sec_folders", folders),
        section("refresh", "set.sec_convert", convert),
        section("pending", "set.sec_auto", auto),
        icon("ok"), esc(t("set.save")))
    return page(t, "%s – Neknihy NextGen" % t("title.settings"), body, "/settings", svc=svc)
