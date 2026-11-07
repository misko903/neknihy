#!/usr/bin/env python3
"""Neknihy web interface + scheduler (no extra dependencies).

    NEKNIHY_WEB_PORT      port to listen on (default 8478)
    NEKNIHY_WEB_PASSWORD  optional password for the web UI (HTTP basic auth,
                          any user name)
    NEKNIHY_CONFIG_DIR    folder for settings (default /config)
    NEKNIHY_LANG          default UI language (cs, sk, en, de); otherwise
                          taken from the browser, fallback cs. Each viewer
                          can switch language in the page header (cookie).

Settings entered in the UI are stored in NEKNIHY_CONFIG_DIR. On first start
NEKNIHY_EMAIL / NEKNIHY_PASSWORD / NEKNIHY_WORKDIR / NEKNIHY_READERDIR /
NEKNIHY_INTERVAL / NEKNIHY_AUTO_RETURN env vars are used as initial values,
as are NEKNIHY_TSK_USERNAME / NEKNIHY_TSK_PASSWORD / NEKNIHY_TSK_LIBRARY for
borrowing e-books in the library catalogue (katalog.tsk.sk).
"""

import base64
import collections
import hmac
import html
import json
import logging
import os
import shutil
import signal
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, quote, urlparse

from neknihy import i18n
from neknihy.app import App
from neknihy.katalog import parse_work_id
from neknihy_cli import env, run_once

log = logging.getLogger("neknihy")

CONFIG_DIR = env("NEKNIHY_CONFIG_DIR", "/config")
CONF_FILE = os.path.join(CONFIG_DIR, "neknihy.conf")
WEB_FILE = os.path.join(CONFIG_DIR, "web.json")
PORT = int(env("NEKNIHY_WEB_PORT", "8478"))
WEB_PASSWORD = env("NEKNIHY_WEB_PASSWORD")
DEFAULT_LANG = i18n.normalize(env("NEKNIHY_LANG"))
FALLBACK_LANG = "cs"  # the app's original language
STATUSES = ("new", "pending", "ok", "expired")


class MemoryLog(logging.Handler):
    def __init__(self, size=300):
        super().__init__()
        self.lines = collections.deque(maxlen=size)

    def emit(self, record):
        self.lines.append(self.format(record))


class Service:
    def __init__(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.running = False
        self.last_run = None
        self.last_error = None  # None = OK, otherwise the error text
        self.next_run = None
        self.app = App(config=CONF_FILE)
        self.web = {"interval": 21600, "auto_return": True}
        self._load_web()
        self._seed_from_env()
        self._ensure_dirs()
        self.app.loadBooks()

    # --- settings -------------------------------------------------------
    def _load_web(self):
        try:
            with open(WEB_FILE) as f:
                self.web.update(json.load(f))
        except FileNotFoundError:
            self.web["interval"] = int(env("NEKNIHY_INTERVAL", "21600"))
            self.web["auto_return"] = env("NEKNIHY_AUTO_RETURN", "1") == "1"
            self._save_web()
        except Exception as e:
            log.warning("Cannot read %s: %s", WEB_FILE, e)

    def _save_web(self):
        with open(WEB_FILE, "w") as f:
            json.dump(self.web, f, indent=2)

    def _seed_from_env(self):
        s = self.app.settings
        s.update(
            email=s.email or env("NEKNIHY_EMAIL"),
            password=s.password or env("NEKNIHY_PASSWORD"),
            workdir=s.workdir or env("NEKNIHY_WORKDIR", "/books"),
            readerdir=s.readerdir or env("NEKNIHY_READERDIR"),
            convert=s.convert if s.convert != "0" else env("NEKNIHY_CONVERT", "0"),
            convertor=s.convertor or env("NEKNIHY_CONVERTOR", "ebook-convert"),
            tsk_username=s.tsk_username or env("NEKNIHY_TSK_USERNAME"),
            tsk_password=s.tsk_password or env("NEKNIHY_TSK_PASSWORD"),
            tsk_library=(s.tsk_library if s.tsk_library not in ("", "vkmr")
                         else env("NEKNIHY_TSK_LIBRARY", "vkmr")),
        )
        try:
            os.chmod(CONF_FILE, 0o600)
        except OSError:
            pass

    def _ensure_dirs(self):
        s = self.app.settings
        for d in (s.workdir, s.readerdir):
            if d:
                try:
                    os.makedirs(d, exist_ok=True)
                except OSError as e:
                    log.warning("Cannot create %s: %s", d, e)

    def save_settings(self, form):
        s = self.app.settings
        password = form.get("password", "")
        tsk_password = form.get("tsk_password", "")
        convert = "1" if form.get("convert") else "0"
        if convert == "1" and shutil.which(form.get("convertor") or s.convertor) is None:
            log.warning("ebook-convert not found, conversion disabled")
            convert = "0"
        with self.lock:
            self.app.updateSettings(
                form.get("email", "").strip(),
                password if password else s.password,
                form.get("workdir", "").strip() or "/books",
                form.get("readerdir", "").strip(),
                convert,
                form.get("convertor", "").strip() or "ebook-convert",
                tsk_username=form.get("tsk_username", "").strip(),
                tsk_password=tsk_password if tsk_password else s.tsk_password,
                tsk_library=form.get("tsk_library", "").strip().lower() or "vkmr",
            )
            try:
                interval = max(0, int(form.get("interval_h", "6") or 0) * 3600)
            except ValueError:
                interval = self.web["interval"]
            self.web["interval"] = interval
            self.web["auto_return"] = bool(form.get("auto_return"))
            self._save_web()
            self._ensure_dirs()
        log.info("Settings saved")
        self.wake.set()  # recompute next run

    # --- actions --------------------------------------------------------
    def action(self, name):
        if not self.app.settings.configured():
            log.error("Not configured: fill in e-mail, password and folder")
            return
        if not self.lock.acquire(blocking=False):
            log.warning("Another operation is running, try again later")
            return
        self.running = True
        try:
            app = self.app
            app.api.logout()  # always start with a fresh login
            if name == "run":
                run_once(app, self.web["auto_return"])
            elif name == "refresh":
                app.refreshRents()
                log.info("Loans refreshed: %d", len(app.books))
            elif name == "download":
                app.downloadBooks()
                log.info("Download finished")
            elif name == "return":
                app.returnBooks()
                log.info("Expired loans removed")
            elif name == "sync":
                r = app.syncReader()
                if r is None:
                    log.warning("Sync folder not set or not found")
                else:
                    log.info("Sync: added %d, removed %d", len(r["added"]), len(r["removed"]))
            self.last_error = None
        except Exception as e:
            log.error("%s failed: %s", name, e)
            self.last_error = str(e)
        finally:
            self.last_run = datetime.now()
            self.running = False
            self.lock.release()
            self.wake.set()

    def borrow(self, work_id):
        """Borrow in the library catalogue, then fetch it from Palmknihy."""
        if not self.lock.acquire(blocking=False):
            log.warning("Another operation is running, try again later")
            return
        self.running = True
        try:
            app = self.app
            app.borrowFromKatalog(work_id)
            if app.settings.configured():
                app.api.logout()
                run_once(app, self.web["auto_return"])
            self.last_error = None
        except Exception as e:
            log.error("borrow failed: %s", e)
            self.last_error = str(e)
        finally:
            self.last_run = datetime.now()
            self.running = False
            self.lock.release()
            self.wake.set()

    def scheduler(self):
        while not self.stop.is_set():
            interval = self.web["interval"]
            now = time.time()
            if self.last_run is None:
                delay = 10  # first run shortly after start
            elif interval <= 0:
                delay = None
            else:
                base = self.last_run.timestamp() if self.last_run else now
                delay = max(10, base + interval - now)
            self.next_run = now + delay if delay is not None else None
            woke = self.wake.wait(delay)
            self.wake.clear()
            if self.stop.is_set():
                break
            if woke:
                continue  # settings changed or manual run, recompute
            if self.app.settings.configured():
                self.action("run")
            else:
                self.last_run = datetime.now()


# --- HTML -----------------------------------------------------------------

# Inline SVG icons (24x24, stroke = currentColor, so they follow the theme
# and the white text of primary buttons).
_ICON_PATHS = {
    "run": "<polygon points='6 4 20 12 6 20 6 4'/>",
    "refresh": "<path d='M21 12a9 9 0 1 1-2.64-6.36L21 8'/><path d='M21 3v5h-5'/>",
    "download": "<path d='M12 3v12'/><path d='m7 10 5 5 5-5'/><path d='M5 21h14'/>",
    "sync": "<rect x='5' y='2' width='14' height='20' rx='2'/><path d='M9 10l3 3 3-3'/>"
            "<path d='M12 6v7'/><path d='M10 18h4'/>",
    "return": "<path d='M9 14 4 9l5-5'/><path d='M4 9h10.5a5.5 5.5 0 0 1 0 11H11'/>",
    "borrow": "<path d='M12 5v14'/><path d='M5 12h14'/>",
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
    "expired": "<circle cx='12' cy='12' r='9'/><path d='m9 9 6 6'/><path d='m15 9-6 6'/>",
    "info": "<circle cx='12' cy='12' r='9'/><path d='M12 11v5'/><path d='M12 8h0'/>",
    "warn": "<path d='M12 3 2 20h20z'/><path d='M12 10v4'/><path d='M12 17h0'/>",
    "log": "<path d='M4 6h16'/><path d='M4 12h16'/><path d='M4 18h10'/>",
    "empty": "<path d='M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z'/>"
             "<path d='M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5'/>",
}


def icon(name, cls="ic"):
    return ("<svg class='%s' viewBox='0 0 24 24' fill='none' stroke='currentColor' "
            "stroke-width='2' stroke-linecap='round' stroke-linejoin='round' "
            "aria-hidden='true'>%s</svg>" % (cls, _ICON_PATHS.get(name, "")))


STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(i18n.__file__)), "static")
STATIC = {"/favicon.ico": "icon-32.png", "/favicon.png": "icon-32.png",
          "/icon.png": "icon-64.png", "/apple-touch-icon.png": "icon-180.png"}


CSS = """
:root{
--bg:#f6f5f2;
--fg:#1d1d1b;
--mut:#6b6a66;
--card:#fff;
--line:#e3e1dc;
--acc:#2f6f4f;
--warn:#a15c00;
--err:#a12a2a}
@media (prefers-color-scheme:dark){
:root{
--bg:#161615;
--fg:#ecebe7;
--mut:#9b9a95;
--card:#1f1f1d;
--line:#33322f;
--acc:#6fbf8f;
--warn:#e0a040;
--err:#e07070}
}
*{
box-sizing:border-box}
body{
margin:0;
background:var(--bg);
color:var(--fg);
font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
header{
display:flex;
align-items:center;
gap:16px;
padding:14px 16px;
border-bottom:1px solid var(--line)}
header h1{
font-size:18px;
margin:0}
header nav{
margin-left:auto;
display:flex;
gap:14px}
a{
color:var(--acc)}
main{
max-width:960px;
margin:0 auto;
padding:16px}
.card{
background:var(--card);
border:1px solid var(--line);
border-radius:10px;
padding:16px;
margin-bottom:16px}
.actions{
display:flex;
flex-wrap:wrap;
gap:8px}
button{
font:inherit;
padding:8px 14px;
border-radius:8px;
border:1px solid var(--line);
background:var(--card);
color:var(--fg);
cursor:pointer}
button.primary{
background:var(--acc);
border-color:var(--acc);
color:#fff}
table{
width:100%;
border-collapse:collapse}
th,td{
text-align:left;
padding:8px 6px;
border-bottom:1px solid var(--line);
vertical-align:top}
th{
color:var(--mut);
font-weight:500;
font-size:13px}
.st-ok{
color:var(--acc)}
.st-pending{
color:var(--warn)}
.st-expired{
color:var(--mut)}
.mut{
color:var(--mut);
font-size:13px}
pre{
white-space:pre-wrap;
font-size:12px;
margin:0;
max-height:320px;
overflow:auto}
label{
display:block;
margin:10px 0 4px;
font-size:13px;
color:var(--mut)}
input[type=text],input[type=email],input[type=password],input[type=number]{
width:100%;
padding:8px;
border:1px solid var(--line);
border-radius:8px;
background:var(--bg);
color:var(--fg);
font:inherit}
header .lang{
display:flex;
gap:6px;
font-size:13px}
header .lang a{
text-decoration:none;
color:var(--mut)}
header .lang a.on{
color:var(--fg);
font-weight:600}
.chk{
display:flex;
gap:8px;
align-items:center;
margin-top:12px;
color:var(--fg);
font-size:15px}
@media (max-width:600px){
.hide-sm{
display:none}
}
.ic{
width:1.1em;
height:1.1em;
flex:none;
vertical-align:-0.18em}
header h1{
display:flex;
align-items:center;
gap:8px}
header h1 img{
width:28px;
height:28px}
header nav a,button,.st,h2{
display:inline-flex;
align-items:center;
gap:6px}
header nav a{
text-decoration:none}
h2{
font-size:14px;
font-weight:500;
color:var(--mut);
margin:0 0 8px}
.empty{
text-align:center;
padding:24px 0}
.empty .ic{
width:40px;
height:40px;
display:block;
margin:0 auto 8px}
.st-new{
color:var(--fg)}
@media (max-width:600px){
header{
flex-wrap:wrap;
gap:8px 14px}
header nav{
order:3;
margin-left:0;
width:100%}
header .lang{
margin-left:auto}
.actions button span{
display:none}
}
"""


def esc(v):
    return html.escape(str(v if v is not None else ""))


def page(t, title, body, path="/"):
    langs = "".join(
        "<a href='/lang?l=%s&amp;next=%s' hreflang='%s' title='%s'%s>%s</a>"
        % (code, quote(path), code, esc(name), " class='on'" if code == t.lang else "",
           code.upper())
        for code, name in i18n.available())
    return ("<!doctype html><html lang='%s'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>%s</title><link rel='icon' type='image/png' href='/favicon.png'>"
            "<link rel='apple-touch-icon' href='/apple-touch-icon.png'>"
            "<style>%s</style></head><body>"
            "<header><h1><img src='/icon.png' alt=''>Neknihy</h1><nav><a href='/'>%s%s</a>"
            "<a href='/settings'>%s%s</a></nav><div class='lang' aria-label='%s'>%s%s</div>"
            "</header><main>%s</main></body></html>"
            % (t.lang, esc(title), CSS, icon("loans"), esc(t("nav.loans")), icon("settings"),
               esc(t("nav.settings")), esc(t("nav.language")), icon("lang"), langs, body))


def fmt_date(t, value):
    try:
        return datetime.fromisoformat(value).strftime(t("fmt.date"))
    except Exception:
        return esc(value)


def render_borrow(svc, t):
    if svc.app.settings.katalog_configured():
        form = ("<form method='post' action='/borrow' class='actions'>"
                "<input type='text' name='work' required style='flex:1;min-width:200px' "
                "placeholder='https://katalog.tsk.sk/…?workId=1439847' aria-label='%s'>"
                "<button class='primary'>%s<span>%s</span></button></form>"
                "<p class='mut'>%s</p>" % (
                    esc(t("borrow.label")), icon("borrow"), esc(t("btn.borrow")),
                    esc(t("borrow.label"))))
    else:
        link = "<a href='/settings'>%s</a>" % esc(t("settings_link"))
        form = "<p class='mut'>%s</p>" % esc(
            t("borrow.not_configured", settings="\x00")).replace("\x00", link)
    return "<div class='card'><h2>%s%s</h2>%s</div>" % (
        icon("borrow"), esc(t("borrow.title")), form)


def render_index(svc, t):
    app = svc.app
    rows = []
    for b in app.books:
        st = b.get("neknihy", {}).get("status", "")
        label = t("status." + st) if st in STATUSES else st
        rows.append(
            "<tr><td>%s<div class='mut'>%s</div></td><td class='hide-sm'>%s</td>"
            "<td><span class='st st-%s'>%s%s</span></td><td class='hide-sm mut'>%s</td></tr>" % (
                esc(b.get("title", b.get("palm_id"))), esc(app.bookAuthors(b)),
                fmt_date(t, b.get("end_time", "")), esc(st),
                icon(st) if st in STATUSES else "", esc(label),
                esc(b.get("neknihy", {}).get("filename", ""))))
    table = ("<table><tr><th>%s</th><th class='hide-sm'>%s</th><th>%s</th>"
             "<th class='hide-sm'>%s</th></tr>%s</table>" % (
                 esc(t("th.book")), esc(t("th.due")), esc(t("th.status")),
                 esc(t("th.file")), "".join(rows))
             if rows else "<p class='mut empty'>%s%s</p>" % (icon("empty"), esc(t("empty"))))
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
    buttons = "".join(
        "<button name='a' value='%s'%s title='%s'>%s<span>%s</span></button>"
        % (a, " class='primary'" if a == "run" else "", esc(t("btn." + a)), icon(a),
           esc(t("btn." + a)))
        for a in ("run", "refresh", "download", "sync", "return"))
    body = ("<div class='card'><form method='post' action='/action' class='actions'>%s</form>"
            "<p class='mut'>%s</p></div>%s<div class='card'>%s</div>"
            "<div class='card'><h2>%s%s</h2><pre>%s</pre></div>" % (
                buttons, "<br>".join(status), render_borrow(svc, t), table, icon("log"),
                esc(t("log.title")), esc("\n".join(reversed(svc.log.lines)))))
    return page(t, "Neknihy", body, "/")


def render_settings(svc, t):
    s = svc.app.settings
    body = """<div class='card'><form method='post' action='/settings'>
<label>%s</label><input type='email' name='email' value='%s' required>
<label>%s %s</label><input type='password' name='password' autocomplete='new-password'>
<label>%s</label><input type='text' name='workdir' value='%s'>
<label>%s</label>
<input type='text' name='readerdir' value='%s'>
<label>%s</label>
<input type='number' min='0' name='interval_h' value='%d'>
<div class='chk'><input type='checkbox' name='auto_return' id='ar'%s>
<label for='ar' style='margin:0'>%s</label></div>
<div class='chk'><input type='checkbox' name='convert' id='cv'%s>
<label for='cv' style='margin:0'>%s</label></div>
<label>%s</label><input type='text' name='convertor' value='%s'>
<h2 style='margin-top:20px'>%s%s</h2>
<label>%s</label><input type='text' name='tsk_username' value='%s' autocomplete='off'>
<label>%s %s</label><input type='password' name='tsk_password' autocomplete='new-password'>
<label>%s</label><input type='text' name='tsk_library' value='%s'>
<p><button class='primary'>%s%s</button></p></form></div>""" % (
        esc(t("set.email")), esc(s.email),
        esc(t("set.password")), esc(t("set.password_keep")) if s.password else "",
        esc(t("set.workdir")), esc(s.workdir),
        esc(t("set.readerdir")), esc(s.readerdir),
        esc(t("set.interval")), svc.web["interval"] // 3600,
        " checked" if svc.web["auto_return"] else "", esc(t("set.auto_return")),
        " checked" if s.convert == "1" else "", esc(t("set.convert")),
        esc(t("set.convertor")), esc(s.convertor),
        icon("borrow"), esc(t("set.tsk")),
        esc(t("set.tsk_username")), esc(s.tsk_username),
        esc(t("set.password")), esc(t("set.password_keep")) if s.tsk_password else "",
        esc(t("set.tsk_library")), esc(s.tsk_library),
        icon("ok"), esc(t("set.save")))
    return page(t, "%s – Neknihy" % t("title.settings"), body, "/settings")


def make_handler(svc):
    class Handler(BaseHTTPRequestHandler):
        server_version = "neknihy"

        def log_message(self, fmt, *args):
            pass

        def _authorized(self):
            if not WEB_PASSWORD:
                return True
            h = self.headers.get("Authorization", "")
            if h.startswith("Basic "):
                try:
                    _, pw = base64.b64decode(h[6:]).decode().split(":", 1)
                    if hmac.compare_digest(pw, WEB_PASSWORD):
                        return True
                except Exception:
                    pass
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Neknihy"')
            self.end_headers()
            return False

        def _same_origin(self):
            origin = self.headers.get("Origin") or self.headers.get("Referer")
            if not origin:
                return True
            return urlparse(origin).netloc == self.headers.get("Host")

        def _send(self, body, code=200):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_static(self, name):
            try:
                with open(os.path.join(STATIC_DIR, name), "rb") as f:
                    data = f.read()
            except OSError:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=604800")
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, to, cookie=None):
            self.send_response(303)
            self.send_header("Location", to)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()

        def _translator(self):
            lang = None
            raw = self.headers.get("Cookie")
            if raw:
                try:
                    c = SimpleCookie(raw)
                    if "neknihy_lang" in c:
                        lang = i18n.normalize(c["neknihy_lang"].value)
                except Exception:
                    pass
            lang = (lang or DEFAULT_LANG
                    or i18n.from_accept_language(self.headers.get("Accept-Language"))
                    or FALLBACK_LANG)
            return i18n.Translator(lang)

        def _error(self, t, code, key):
            self._send(page(t, str(code), "<p>%s</p>" % esc(t(key))), code)

        def do_GET(self):
            url = urlparse(self.path)
            path = url.path
            if path == "/health":
                self._send("ok")
                return
            if path in STATIC:
                self._send_static(STATIC[path])
                return
            if not self._authorized():
                return
            t = self._translator()
            if path == "/":
                self._send(render_index(svc, t))
            elif path == "/settings":
                self._send(render_settings(svc, t))
            elif path == "/lang":
                q = parse_qs(url.query)
                lang = i18n.normalize(q.get("l", [""])[0])
                nxt = q.get("next", ["/"])[0]
                if nxt not in ("/", "/settings"):
                    nxt = "/"  # no open redirects
                cookie = ("neknihy_lang=%s; Path=/; Max-Age=31536000; SameSite=Lax" % lang
                          if lang else None)
                self._redirect(nxt, cookie)
            else:
                self._error(t, 404, "err.not_found")

        def do_POST(self):
            if not self._authorized():
                return
            t = self._translator()
            if not self._same_origin():
                self._error(t, 403, "err.forbidden")
                return
            length = min(int(self.headers.get("Content-Length", 0) or 0), 65536)
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
            path = urlparse(self.path).path
            if path == "/settings":
                svc.save_settings(form)
                self._redirect("/")
            elif path == "/action":
                a = form.get("a", "")
                if a in ("run", "refresh", "download", "sync", "return"):
                    threading.Thread(target=svc.action, args=(a,), daemon=True).start()
                    time.sleep(0.3)
                self._redirect("/")
            elif path == "/borrow":
                work_id = parse_work_id(form.get("work", ""))
                if work_id is None:
                    log.error("Not a catalogue link or work ID: %s", form.get("work", ""))
                else:
                    threading.Thread(target=svc.borrow, args=(work_id,), daemon=True).start()
                    time.sleep(0.3)
                self._redirect("/")
            else:
                self._error(t, 404, "err.not_found")

    return Handler


def main():
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    mem = MemoryLog()
    mem.setFormatter(fmt)
    logging.getLogger().addHandler(mem)

    svc = Service()
    svc.log = mem
    threading.Thread(target=svc.scheduler, daemon=True).start()

    server = ThreadingHTTPServer(("0.0.0.0", PORT), make_handler(svc))

    def handle_signal(signum, frame):
        svc.stop.set()
        svc.wake.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    log.info("Web UI on port %d%s", PORT, " (password protected)" if WEB_PASSWORD else "")
    server.serve_forever()


if __name__ == "__main__":
    main()
