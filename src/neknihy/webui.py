"""HTML rendering of the Neknihy NextGen web interface.

The pages are built on the server as plain strings; styles and scripts are
the static files app.css and app.js next to the icons.
"""

import hashlib
import html
import logging
import os
import re
from datetime import datetime
from urllib.parse import parse_qs, quote

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
    "close": "<path d='M6 6l12 12'/><path d='M18 6 6 18'/>",
    "link": "<path d='M14 4h6v6'/><path d='M20 4 10 14'/><path d='M18 14v5a1 1 0 0 1-1 1H5a1 "
            "1 0 0 1-1-1V7a1 1 0 0 1 1-1h5'/>",
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


# --- loans: deadlines, statuses, grouping -----------------------------------

DATE_RE = re.compile(r"^\d{4}-?\d{2}-?\d{2}")
SOON_DAYS = 3  # this many days left or fewer is shown as urgent


def days_left(end_time, now, parse):
    """Whole calendar days from ``now`` to the deadline (0 = today, negative
    = past) in ``now``'s time zone, or None when there is no usable date.
    ``parse`` is App.dateFromString, which returns "now" for junk, so the
    text is checked to look like a date first."""
    if not end_time or not DATE_RE.match(str(end_time)):
        return None
    try:
        end = parse(end_time)
        if end.tzinfo is None:
            end = end.astimezone()  # naive = local time
        return (end.astimezone(now.tzinfo).date() - now.date()).days
    except Exception:
        return None


def due_text(t, days):
    """"dnes", "zajtra", "3 dni", "7 dní"; "–" when unknown."""
    if days is None:
        return "–"
    if days == 0:
        return t("due.today")
    if days == 1:
        return t("due.tomorrow")
    return t("due.days_few" if 2 <= abs(days) <= 4 else "due.days_many", n=days)


def is_soon(days):
    return days is not None and 0 <= days <= SOON_DAYS


def status_key(book):
    st = (book.get("neknihy") or {}).get("status", "")
    return st if st in STATUSES else "other"


def split_loans(books, now, parse):
    """(active, expired) without deleted loans; the active ones by deadline,
    those without a deadline last."""
    active, expired = [], []
    for b in books:
        st = (b.get("neknihy") or {}).get("status", "")
        if st == "deleted":
            continue
        (expired if st == "expired" else active).append(b)
    far = float("inf")
    active.sort(key=lambda b: (lambda d: far if d is None else d)(
        days_left(b.get("end_time"), now, parse)))
    return active, expired


def loan_key(book):
    """Unique key of a loan; e-books and audiobooks can share a palm_id."""
    return "%s-%s" % (book.get("palm_id", ""), "a" if isAudiobook(book) else "e")


def esc(v):
    return html.escape(str(v if v is not None else ""))


KIND_ICONS = {"ebook": "ebook", "audiobook": "audio", "all": "both"}


def search_form(t, query="", kind="ebook"):
    """The big catalogue search box with the e-book / audiobook / all switch
    (changing it searches again when there is a query)."""
    options = "".join(
        "<input type='radio' name='kind' value='%s' id='kind-%s'%s>"
        "<label for='kind-%s'>%s<span>%s</span></label>" % (
            k, k, " checked" if k == kind else "", k, icon(KIND_ICONS[k]), esc(t("kind." + k)))
        for k in katalog.KINDS)
    return ("<form method='get' action='/search' class='bigsearch' role='search' "
            "onchange='if(this.q.value.trim())this.submit()'>"
            "<span class='withic'>%s<input type='search' name='q' value='%s' required%s "
            "placeholder='%s' aria-label='%s'></span>"
            "<span class='seg' role='radiogroup' aria-label='%s'>%s</span>"
            "<button class='primary'>%s%s</button></form>" % (
                icon("search"), esc(query), "" if query else " autofocus",
                esc(t("search.placeholder")), esc(t("btn.search")),
                esc(t("kind.label")), options, icon("search"), esc(t("btn.search"))))


def borrow_form(svc, t):
    """Borrowing by a catalogue link or workId (in the loans ⋮ menu)."""
    s = svc.app.settings
    if not katalog.can_borrow(s.tsk_library):
        return "<p class='mut'>%s</p>" % esc(t("borrow.unsupported"))
    if not s.katalog_configured():
        return not_configured_hint(t)
    return ("<form method='post' action='/borrow' class='borrow'><label for='work'>%s</label>"
            "<div class='actions'><input type='text' name='work' id='work' required "
            "placeholder='https://katalog.tsk.sk/…?workId=1439847'>"
            "<button class='primary'>%s<span>%s</span></button></div></form>" % (
                esc(t("borrow.label")), icon("borrow"), esc(t("btn.borrow"))))


def local_now():
    return datetime.now().astimezone()


ERROR_CHARS = 80  # of the last error shown in the sidebar (all of it in title)


def sync_block(svc, t):
    """Sync state, last/next time and the one Synchronise button."""
    app = svc.app
    if not app.settings.configured():
        link = "<a href='/settings'>%s</a>" % esc(t("settings_link"))
        hint = esc(t("not_configured.hint", settings="\x00")).replace("\x00", link)
        return ("<div class='sync'><p class='st st-pending'>%s<b>%s</b></p>"
                "<p class='mut'>%s</p></div>" % (icon("warn"), esc(t("not_configured")), hint))
    if svc.running:
        state = "<p class='st st-pending'>%s%s</p>" % (icon("pending"), esc(t("sync.running")))
    elif svc.last_error:
        first = str(svc.last_error).splitlines()[0] if str(svc.last_error).strip() else ""
        short = first if len(first) <= ERROR_CHARS else first[:ERROR_CHARS] + "…"
        state = ("<p class='st st-expired'>%s%s</p><p class='err' title='%s'>%s</p>" % (
            icon("warn"), esc(t("sync.error")), esc(svc.last_error), esc(short)))
    elif svc.last_run:
        state = "<p class='st st-ok'>%s%s</p>" % (icon("ok"), esc(t("sync.ok")))
    else:
        state = ""
    times = []
    if svc.last_run:
        times.append(esc(t("sync.last", time=svc.last_run.strftime(t("fmt.datetime")))))
    if svc.next_run and svc.web["interval"] > 0:
        times.append(esc(t("sync.next", time=datetime.fromtimestamp(
            svc.next_run).strftime(t("fmt.datetime")))))
    return ("<div class='sync'>%s%s<form method='post' action='/action'>"
            "<button name='a' value='run' class='primary'>%s%s</button></form></div>" % (
                state, "<p class='mut'>%s</p>" % "<br>".join(times) if times else "",
                icon("refresh"), esc(t("btn.sync_now"))))


def sidebar(svc, t, path):
    """Brand, navigation, sync state, log link and theme toggle."""
    active, _ = split_loans(svc.app.books, local_now(), svc.app.dateFromString)

    def link(href, ic, key, extra="", attrs=""):
        cur = " aria-current='page'" if href == path else ""
        return "<a href='%s'%s%s>%s<span>%s</span>%s</a>" % (
            href, cur, attrs, icon(ic), esc(t(key)), extra)
    nav = (link("/", "loans", "nav.loans", "<span class='count'>%d</span>" % len(active))
           + link("/search", "search", "nav.search", attrs=" id='nav-search'")
           + link("/settings", "settings", "nav.settings"))
    theme = ("<button type='button' class='theme' onclick='nkTheme()' %s>%s%s%s</button>" % (
        " ".join("data-%s='%s: %s'" % (m, esc(t("theme.toggle")), esc(t("theme." + m)))
                 for m in THEMES),
        icon("auto", "ic t-auto"), icon("sun", "ic t-light"), icon("moon", "ic t-dark")))
    log_cur = " aria-current='page'" if path == "/log" else ""
    return ("<aside class='side'><a href='/' class='brand'><img src='/icon.png?v=%s' alt=''>"
            "<span>Neknihy</span></a><nav>%s</nav>%s"
            "<div class='foot'><a href='/log'%s>%s<span>%s</span></a>%s</div></aside>" % (
                STATIC_VER, nav, sync_block(svc, t), log_cur, icon("log"),
                esc(t("log.title")), theme))


def page(t, title, body, path="/", refresh=None, svc=None, detail=""):
    """The whole page: sidebar, content and, when ``detail`` (the panel's
    <template>) is given, the detail panel app.js fills from a list item."""
    meta = "<meta http-equiv='refresh' content='%d'>" % refresh if refresh else ""
    return ("<!doctype html><html lang='%s'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>%s"
            "<title>%s</title><link rel='icon' type='image/png' href='/favicon.png?v=%s'>"
            "<link rel='apple-touch-icon' href='/apple-touch-icon.png?v=%s'>"
            "<link rel='stylesheet' href='/app.css?v=%s'><script>%s</script></head><body>"
            "<div class='shell'>%s<main>%s</main>%s</div>"
            "<script src='/app.js?v=%s'></script></body></html>"
            % (t.lang, meta, esc(title), STATIC_VER, STATIC_VER, STATIC_VER, THEME_JS,
               sidebar(svc, t, path) if svc else "", body,
               "<aside id='detail' class='detail' hidden>%s</aside>" % detail if detail else "",
               STATIC_VER))


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


# Ways to show a list of books (see the table[data-view] CSS and app.js)
VIEWS = (("list", "v-compact"), ("shelf", "v-grid"))
FILTER_OVER = 8  # the filter box shows only with more loans than this


def view_switch(t, table_ids):
    """List / shelf switch for the given tables (app.js remembers it)."""
    views = "".join(
        "<input type='radio' name='view' value='%s' id='view-%s'%s>"
        "<label for='view-%s'>%s<span>%s</span></label>" % (
            v, v, " checked" if v == "list" else "", v, icon(ic), esc(t("view." + v)))
        for v, ic in VIEWS)
    return ("<span class='seg' role='radiogroup' aria-label='%s' data-view-for='%s'>%s</span>"
            % (esc(t("view.label")), esc(" ".join(table_ids)), views))


def filter_box(t, table_id):
    """Text box that hides the rows of a table that do not match (app.js)."""
    return ("<div class='actions tfilter'><span class='withic' title='%s'>%s"
            "<input type='search' data-for='%s' placeholder='%s' aria-label='%s' "
            "autocomplete='off'></span>"
            "<span class='mut' data-count='%s' hidden></span></div>" % (
                esc(t("filter.placeholder")), icon("filter"),
                table_id, esc(t("filter.hint")), esc(t("filter.placeholder")),
                esc(t("filter.count", n="{n}", total="{total}"))))


def cover_html(src, title):
    """Book cover, or a placeholder box with the title when there is none."""
    if src and str(src).startswith("https://"):
        return "<img src='%s' alt='' loading='lazy' class='cover'>" % esc(src)
    return "<span class='ph'>%s</span>" % esc(title)


def data_attrs(values):
    """data-* attributes of a list item, read by the detail panel."""
    return "".join(" data-%s='%s'" % (k, esc(v if v is not None else ""))
                   for k, v in values.items())


def book_cell(cover, title, authors, audio, sub=""):
    return ("<td class='c-title' data-v='%s'><div class='bk'>%s<div><div class='t'>%s%s</div>"
            "<div class='a'>%s</div>%s</div></div></td>" % (
                esc(title), cover, icon("audio") if audio else "", esc(title), esc(authors),
                sub))


def status_pill(key, label):
    ic = {"new": "new", "pending": "pending", "ok": "ok", "expired": "expired",
          "awaiting": "pending"}.get(key, "info")
    return "<span class='pill st-%s'>%s%s</span>" % (esc(key), icon(ic), esc(label))


NO_DAYS = 99999  # sort value of a loan without a deadline (last)


def loan_row(svc, t, book, now):
    """One loan in the loans table / shelf."""
    app = svc.app
    st = status_key(book)
    label = t("status." + st) if st != "other" else (book.get("neknihy") or {}).get("status", "")
    end = book.get("end_time") or ""
    days = days_left(end, now, app.dateFromString)
    title = book.get("title") or str(book.get("palm_id", ""))
    authors = app.bookAuthors(book)
    audio = isAudiobook(book)
    wid = webId(book)
    src = book.get("cover") or book.get("base_image_url") or ""
    date = fmt_date(t, end) if days is not None else ""
    shown = date if st == "expired" and days is not None else due_text(t, days)
    attrs = data_attrs({
        "key": loan_key(book), "title": title, "authors": authors,
        "kind": "audio" if audio else "ebook",
        "kindlabel": t("kind.audio_tag" if audio else "kind.ebook_tag"),
        "due": "%s · %s" % (date, due_text(t, days)) if date else "–",
        "status": label, "st": st, "file": (book.get("neknihy") or {}).get("filename", ""),
        "cover": src if str(src).startswith("https://") else "",
        "link": "https://www.palmknihy.cz/kniha/%s" % wid if wid else "",
        "palm": book.get("palm_id", ""), "audio": "1" if audio else "0"})
    return ("<tr class='item' tabindex='0'%s>%s<td class='due%s' data-v='%d'>%s</td>%s"
            "<td data-v='%s'>%s</td></tr>" % (
                attrs, book_cell(cover_html(src, title), title, authors, audio),
                " soon" if is_soon(days) and st != "expired" else "",
                NO_DAYS if days is None else days, esc(shown), rating_cell(svc, t, wid),
                esc(label), status_pill(st, label)))


def awaiting_row(t, aw):
    """A loan borrowed in the catalogue that Palmknihy does not list yet."""
    title = aw.get("title") or "workId %s" % aw.get("work_id")
    sub = "<div class='a'>%s</div>" % esc(t("await." + aw.get("state", "waiting"), s=WAIT_STEP))
    label = t("status.awaiting")
    return ("<tr class='await'>%s<td class='due' data-v='-1'>–</td>"
            "<td class='rt mut' data-v='-1'>–</td><td data-v=''>%s</td></tr>" % (
                book_cell(cover_html("", title), title, "", False, sub),
                status_pill("awaiting", label)))


def loans_table(t, table_id, rows):
    return ("<table class='sortable loans' id='%s' data-view='list' %s><thead><tr>%s%s%s"
            "<th>%s</th></tr></thead><tbody>%s</tbody></table>" % (
                table_id, rating_attrs(t), sort_th(t("th.book")),
                sort_th(t("th.left"), "num"), sort_th(t("th.rating"), "num", first="desc"),
                esc(t("th.status")), "".join(rows)))


def actions_menu(svc, t):
    """⋮ menu with the single steps of a sync and borrowing by link."""
    buttons = "".join(
        "<button name='a' value='%s'>%s%s</button>" % (a, icon(a), esc(t("btn." + a)))
        for a in ("refresh", "download", "sync", "return"))
    return ("<details class='menu'><summary title='%s' aria-label='%s'>%s</summary>"
            "<div class='pop'><form method='post' action='/action' class='acts'>%s</form>%s"
            "</div></details>" % (esc(t("nav.actions")), esc(t("nav.actions")), icon("more"),
                                  buttons, borrow_form(svc, t)))


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


def search_page(svc, t, query_string):
    """GET /search: the query string parsed and the page rendered."""
    q = parse_qs(query_string)
    query = q.get("q", [""])[0].strip()[:200]
    try:
        page_no = max(1, int(q.get("page", ["1"])[0]))
    except ValueError:
        page_no = 1
    kind = q.get("kind", ["ebook"])[0]
    if kind not in katalog.KINDS:
        kind = "ebook"
    return render_search(svc, t, query, page_no, kind)


def search_detail(t):
    """<template> of the search result detail panel (filled in by app.js)."""
    return ("<template id='search-tpl'>%s<dl>%s%s%s</dl><div class='dacts'>"
            "<form method='post' action='/borrow' data-eq='action:borrow'>"
            "<input type='hidden' name='work' data-val='work'>"
            "<input type='hidden' name='palm_id' data-val='palm'>"
            "<input type='hidden' name='title' data-val='title'>"
            "<button class='primary'>%s%s</button></form>"
            "<a class='btn' target='_blank' rel='noopener' data-href='link'>%s%s</a>"
            "</div></template>" % (
                detail_head(t).replace("<span class='pill' data-st='st' data-f='status'></span>",
                                       "<span class='pill' data-st='st' data-f='status' "
                                       "data-show='status'></span>"),
                detail_row(t, "detail.kind", "kindlabel"),
                detail_row(t, "detail.published", "pub", show="pub"),
                detail_row(t, "detail.rating", "rating"),
                icon("borrow"), esc(t("btn.borrow")), icon("link"),
                esc(t("detail.open_catalog"))))


def result_row(svc, t, r, action, library):
    """One catalogue search result; ``action`` is borrow, loaned, waiting
    or none."""
    title = r.get("title") or str(r.get("work_id"))
    authors = "; ".join(r.get("authors") or [])
    audio = bool(r.get("audiobook"))
    label = {"loaned": t("search.loaned"), "waiting": t("search.borrowed_wait")}.get(action, "")
    st = {"loaned": "ok", "waiting": "awaiting"}.get(action, "")
    attrs = data_attrs({
        "key": "w%s" % r.get("work_id"), "title": title, "authors": authors,
        "kind": "audio" if audio else "ebook",
        "kindlabel": t("kind.audio_tag" if audio else "kind.ebook_tag"),
        "pub": r.get("publish_info") or "", "cover": r.get("cover") or "",
        "link": katalog.detail_url(library, r), "action": action, "work": r.get("work_id"),
        "palm": "" if audio or r.get("palm_id") is None else r.get("palm_id"),
        "status": label, "st": st})
    if action == "borrow":
        cell = ("<form method='post' action='/borrow'>"
                "<input type='hidden' name='work' value='%s'>"
                "<input type='hidden' name='palm_id' value='%s'>"
                "<input type='hidden' name='title' value='%s'>"
                "<button class='primary'>%s%s</button></form>" % (
                    esc(r.get("work_id")),
                    "" if audio or r.get("palm_id") is None else esc(r.get("palm_id")),
                    esc(title), icon("borrow"), esc(t("btn.borrow"))))
    elif label:
        cell = status_pill(st, label)
    else:
        cell = ""
    sub = "<div class='a'>%s</div>" % esc(r.get("publish_info") or "")
    return ("<tr class='item' tabindex='0'%s>%s%s<td class='c-act'>%s</td></tr>" % (
        attrs, book_cell(cover_html(r.get("cover"), title), title, authors, audio, sub),
        rating_cell(svc, t, r.get("palm_id")), cell))


def render_search(svc, t, query, page_no, kind="ebook"):
    app = svc.app
    s = app.settings
    library = s.tsk_library or "vkmr"
    head = ("<div class='head'><h1>%s</h1><div class='tools'>%s</div></div>%s" % (
        esc(t("search.title")), view_switch(t, ["search"]), search_form(t, query, kind)))

    def done(html_body, detail=""):
        title = "%s – Neknihy NextGen" % t("btn.search")
        return page(t, title, head + html_body, "/search", svc=svc, detail=detail)
    if not query:
        return done("")
    try:
        res = katalog.search(query, library=library, page=page_no, kind=kind)
    except Exception as e:
        log.error("Catalogue search failed: %s", e)
        return done("<p class='st st-expired'>%s%s</p>" % (
            icon("warn"), esc(t("search.error", error=e))))

    active = [b for b in app.books
              if (b.get("neknihy") or {}).get("status") not in ("expired", "deleted")]
    # the catalogue links to palmknihy.cz/kniha/<id>, which is often not the
    # loan's palm_id (see catalogueIds); audiobooks are matched by title too
    loaned = set().union(*(catalogueIds(b) for b in active))
    loaned_audio = {(b.get("title") or "").strip().lower() for b in active if isAudiobook(b)}
    borrow_here = katalog.can_borrow(library)
    can_borrow = borrow_here and s.katalog_configured()
    waiting = {aw["work_id"] for aw in svc.awaiting}
    rows = []
    for r in res["results"]:
        if r["work_id"] in waiting:
            action = "waiting"
        elif ((r.get("palm_id") is not None and r["palm_id"] in loaned) or
              (r.get("audiobook") and (r.get("title") or "").strip().lower() in loaned_audio)):
            action = "loaned"
        elif r.get("ebook") and can_borrow:
            action = "borrow"
        else:
            action = "none"
        rows.append(result_row(svc, t, r, action, library))
    if rows:
        body = ("<p class='mut'>%s</p><table class='sortable loans results' id='search' "
                "data-view='list' %s><thead><tr>%s%s<th></th></tr></thead><tbody>%s</tbody>"
                "</table>" % (
                    esc(t("search.results_" + res["kind"], n=res["total"])), rating_attrs(t),
                    sort_th(t("th.book")), sort_th(t("th.rating"), "num", first="desc"),
                    "".join(rows)))
    else:
        body = "<div class='empty'>%s<p>%s</p></div>" % (icon("empty"), esc(t("search.none")))
    if res["pages"] > 1:
        links = []
        for n in range(1, res["pages"] + 1):
            links.append("<b>%d</b>" % n if n == page_no else
                         "<a href='/search?q=%s&amp;kind=%s&amp;page=%d'>%d</a>"
                         % (quote(query), quote(res["kind"]), n, n))
        body += "<p class='pages'>%s</p>" % " ".join(links)
    if not borrow_here:
        body += "<p class='mut'>%s</p>" % esc(t("borrow.unsupported"))
    elif not can_borrow:
        body += not_configured_hint(t)
    return done(body, search_detail(t) if rows else "")


def detail_head(t):
    """Top of a detail panel: close button, cover, title, authors, status."""
    return ("<button type='button' class='close' data-close title='%s' aria-label='%s'>%s</button>"
            "<img class='dcover' alt='' data-src='cover'>"
            "<h2 data-f='title'></h2><p class='dauth' data-f='authors'></p>"
            "<span class='pill' data-st='st' data-f='status'></span>" % (
                esc(t("detail.close")), esc(t("detail.close")), icon("close")))


def detail_row(t, key, field, show=None):
    """A label / value pair; ``show`` hides it when that field is empty."""
    cond = " data-show='%s'" % show if show else ""
    return "<dt%s>%s</dt><dd%s data-f='%s'></dd>" % (cond, esc(t(key)), cond, field)


def loan_detail(t):
    """<template> of the loan detail panel (filled in by app.js)."""
    return ("<template id='detail-tpl'>%s<dl>%s%s%s%s</dl><div class='dacts'>"
            "<a class='btn' target='_blank' rel='noopener' data-href='link'>%s%s</a>"
            "<form method='post' action='/delete' class='del' data-confirm-tpl='%s'>"
            "<input type='hidden' name='palm_id' data-val='palm'>"
            "<input type='hidden' name='audio' data-val='audio'>"
            "<button class='danger'>%s%s</button></form></div></template>" % (
                detail_head(t), detail_row(t, "detail.kind", "kindlabel"),
                detail_row(t, "detail.due", "due"), detail_row(t, "detail.rating", "rating"),
                detail_row(t, "detail.file", "file", show="file"), icon("link"),
                esc(t("detail.open_palm")), esc(t("delete.confirm", title="{title}")),
                icon("delete"), esc(t("btn.delete"))))


def render_index(svc, t):
    app = svc.app
    now = local_now()
    active, expired = split_loans(app.books, now, app.dateFromString)
    awaiting = list(svc.awaiting)
    tables = ["loans"] + (["expired"] if expired else [])
    head = ("<div class='head'><h1>%s</h1><div class='tools'>%s%s</div></div>" % (
        esc(t("nav.loans")), view_switch(t, tables), actions_menu(svc, t)))
    if not (active or expired or awaiting):
        body = head + ("<div class='empty'>%s<p>%s</p><a class='btn' href='/search'>%s%s</a>"
                       "</div>" % (icon("empty"), esc(t("loans.empty")), icon("search"),
                                   esc(t("loans.empty_link"))))
    else:
        rows = [awaiting_row(t, aw) for aw in awaiting]
        rows += [loan_row(svc, t, b, now) for b in active]
        body = head
        if len(active) + len(expired) > FILTER_OVER:
            body += filter_box(t, "loans")
        body += loans_table(t, "loans", rows)
        if expired:
            body += ("<div class='section'><h2>%s%s</h2>%s</div>" % (
                icon("expired"), esc(t("loans.expired_title")),
                loans_table(t, "expired", [loan_row(svc, t, b, now) for b in expired])))
    busy = svc.running or svc.awaiting
    return page(t, "Neknihy NextGen", body, "/", refresh=PAGE_REFRESH if busy else None,
                svc=svc, detail=loan_detail(t))


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
