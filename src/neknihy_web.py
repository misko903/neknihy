#!/usr/bin/env python3
"""Neknihy web interface + scheduler (no extra dependencies).

    NEKNIHY_WEB_PORT      port to listen on (default 8478)
    NEKNIHY_WEB_PASSWORD  optional password for the web UI (HTTP basic auth,
                          any user name)
    NEKNIHY_CONFIG_DIR    folder for settings (default /config)
    NEKNIHY_LANG          default UI language (cs, sk, en, de); otherwise
                          taken from the browser, fallback cs. Each viewer
                          can pick a language or "Automatic (browser)" in
                          Settings (stored per browser in a cookie).
    NEKNIHY_WAIT_STEP     after borrowing in the catalogue, seconds between
                          checks of the Palmknihy loans (default 20)
    NEKNIHY_WAIT_MAX      how long to keep checking for the new loan, in
                          seconds (default 1800)

Settings entered in the UI are stored in NEKNIHY_CONFIG_DIR. On first start
NEKNIHY_EMAIL / NEKNIHY_PASSWORD / NEKNIHY_WORKDIR / NEKNIHY_READERDIR /
NEKNIHY_AUDIODIR / NEKNIHY_INTERVAL / NEKNIHY_AUTO_RETURN env vars are used
as initial values (with no NEKNIHY_AUDIODIR, a mounted Audiobookshelf library
folder /audiobooks is used for audiobooks),
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
from neknihy.api import catalogueIds, isAudiobook, webId
from neknihy.app import App
from neknihy import katalog
from neknihy.katalog import parse_work_id
from neknihy.rating import MAX_IDS, Ratings
from neknihy_cli import env, run_once, sync

log = logging.getLogger("neknihy")

CONFIG_DIR = env("NEKNIHY_CONFIG_DIR", "/config")
CONF_FILE = os.path.join(CONFIG_DIR, "neknihy.conf")
WEB_FILE = os.path.join(CONFIG_DIR, "web.json")
PORT = int(env("NEKNIHY_WEB_PORT", "8478"))
WEB_PASSWORD = env("NEKNIHY_WEB_PASSWORD")
DEFAULT_LANG = i18n.normalize(env("NEKNIHY_LANG"))
FALLBACK_LANG = "cs"  # the app's original language
LANG_COOKIE = "neknihy_lang=%s; Path=/; Max-Age=31536000; SameSite=Lax"
STATUSES = ("new", "pending", "ok", "expired")
# Where the Audiobookshelf library is usually mounted in the container; used
# as the audiobook folder on first start if it exists.
AUDIOBOOKSHELF_DIRS = ("/audiobooks",)
WAIT_STEP = int(env("NEKNIHY_WAIT_STEP", "20"))  # s between checks after a loan
WAIT_MAX = int(env("NEKNIHY_WAIT_MAX", "1800"))  # s to wait for a new loan
PAGE_REFRESH = 10  # s, auto-reload of the loans page while something is going on


class MemoryLog(logging.Handler):
    def __init__(self, size=300):
        super().__init__()
        self.lines = collections.deque(maxlen=size)

    def emit(self, record):
        self.lines.append(self.format(record))


def default_audiodir():
    """NEKNIHY_AUDIODIR, else a mounted Audiobookshelf library folder, else ""."""
    value = env("NEKNIHY_AUDIODIR")
    if value:
        return value
    for d in AUDIOBOOKSHELF_DIRS:
        if os.path.isdir(d):
            log.info("Audiobookshelf folder %s found, audiobooks go there", d)
            return d
    return ""


class Service:
    def __init__(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.running = False
        self.awaiting = []  # loans borrowed in the catalogue, waiting for Palmknihy
        self.last_run = None
        self.last_error = None  # None = OK, otherwise the error text
        self.next_run = None
        self.app = App(config=CONF_FILE)
        self.ratings = Ratings(os.path.join(CONFIG_DIR, "ratings.json"))
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
        if "ingest" not in self.web:
            self.web["ingest"] = env("NEKNIHY_INGEST", "0") == "1"
            self._save_web()

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
        # only once, so clearing the folder in the web UI sticks
        if not self.web.get("audiodir_seeded"):
            s.update(audiodir=s.audiodir or default_audiodir())
            self.web["audiodir_seeded"] = True
            self._save_web()
        try:
            os.chmod(CONF_FILE, 0o600)
        except OSError:
            pass

    def _ensure_dirs(self):
        s = self.app.settings
        for d in (s.workdir, s.readerdir, s.audiodir):
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
                audiodir=form.get("audiodir", "").strip(),
            )
            try:
                interval = max(0, int(form.get("interval_h", "6") or 0) * 3600)
            except ValueError:
                interval = self.web["interval"]
            self.web["interval"] = interval
            self.web["auto_return"] = bool(form.get("auto_return"))
            self.web["ingest"] = bool(form.get("ingest"))
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
                run_once(app, self.web["auto_return"], self.web.get("ingest", False))
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
                if not app.settings.readerdir:
                    log.warning("Sync folder not set")
                sync(app, self.web.get("ingest", False))
            self.last_error = None
        except Exception as e:
            log.error("%s failed: %s", name, e)
            self.last_error = str(e)
        finally:
            self.last_run = datetime.now()
            self.running = False
            self.lock.release()
            self.wake.set()

    def delete(self, key):
        """Delete one loan's file (see App.deleteBook); key is (audiobook, palm_id as text)."""
        if not self.lock.acquire(blocking=False):
            log.warning("Another operation is running, try again later")
            return
        try:
            for i, b in enumerate(self.app.books):
                if (isAudiobook(b), str(b.get("palm_id"))) == key:
                    title = b.get("title") or b.get("palm_id")
                    self.app.deleteBook(i)
                    log.info("Deleted: %s", title)
                    break
        except Exception as e:
            log.error("Delete failed: %s", e)
            self.last_error = str(e)
        finally:
            self.lock.release()

    @staticmethod
    def _loan_key(book):
        # e-books and audiobooks have separate palm_id ranges
        return (isAudiobook(book), book.get("palm_id"))

    def _active_ids(self):
        return {self._loan_key(b) for b in self.app.books
                if b.get("neknihy", {}).get("status") != "expired"}

    def _find_awaited(self, aw):
        """The awaited book in the loans list: the loan the catalogue ID
        points to, otherwise the first active loan that was not there
        before (the catalogue ID is not always one the loan carries)."""
        new = None
        for b in self.app.books:
            if b.get("neknihy", {}).get("status") == "expired":
                continue
            if aw["palm_id"] is not None and aw["palm_id"] in catalogueIds(b):
                return b
            if new is None and self._loan_key(b) not in aw["known"]:
                new = b
        return new

    def borrow(self, work_id, palm_id=None, title=""):
        """Borrow in the library catalogue, then wait until Palmknihy lists the
        loan and download it right away."""
        if not self.lock.acquire(blocking=False):
            log.warning("Another operation is running, try again later")
            return
        self.running = True
        borrowed = False
        # shown in the UI right away; state: borrowing -> waiting -> found
        aw = {"work_id": work_id, "palm_id": palm_id, "title": title,
              "known": self._active_ids(), "since": time.time(), "state": "borrowing"}
        self.awaiting.append(aw)
        try:
            self.app.borrowFromKatalog(work_id)
            borrowed = True
            aw["state"] = "waiting"
            aw["since"] = time.time()
            self.last_error = None
        except Exception as e:
            log.error("borrow failed: %s", e)
            self.last_error = str(e)
            self.awaiting.remove(aw)
        finally:
            self.last_run = datetime.now()
            self.running = False
            self.lock.release()
            self.wake.set()
        if borrowed:
            self._wait_for_loan(aw)

    def _wait_for_loan(self, aw):
        """Refresh the Palmknihy loans every WAIT_STEP seconds until the
        borrowed book is listed and downloaded (or WAIT_MAX runs out)."""
        name = aw["title"] or "workId=%s" % aw["work_id"]
        if not self.app.settings.configured():
            log.warning("Palmknihy login not set, the borrowed book cannot be downloaded")
            self.awaiting.remove(aw)
            return
        log.info("Waiting for the loan to appear in Palmknihy: %s", name)
        app = self.app
        app.api.logout()  # start with a fresh login
        first = True
        try:
            while not self.stop.is_set():
                if not first and self.stop.wait(WAIT_STEP):
                    return
                first = False
                if time.time() - aw["since"] > WAIT_MAX:
                    log.warning("The loan did not appear in Palmknihy within %d min: %s. "
                                "It will be downloaded by the next run.", WAIT_MAX // 60, name)
                    return
                if not self.lock.acquire(timeout=WAIT_STEP):
                    continue  # another operation is running, try next round
                self.running = True
                try:
                    app.api.forgetRents()  # keep the login, fetch the loans again
                    app.refreshRents()
                    book = self._find_awaited(aw)
                    if book is None:
                        log.info("Loan not listed in Palmknihy yet, checking again in %d s",
                                 WAIT_STEP)
                        continue
                    aw["state"] = "found"
                    aw["title"] = book.get("title") or aw["title"]
                    run_once(app, self.web["auto_return"], self.web.get("ingest", False))
                    if book.get("neknihy", {}).get("status") == "ok":
                        log.info("Borrowed book downloaded: %s", book["neknihy"]["filename"])
                        self.last_error = None
                        return
                    log.info("Loan listed but the file is not available yet, "
                             "checking again in %d s", WAIT_STEP)
                except Exception as e:
                    log.error("Waiting for loan: %s", e)
                    self.last_error = str(e)
                    app.api.logout()  # fresh login next round
                finally:
                    self.last_run = datetime.now()
                    self.running = False
                    self.lock.release()
        finally:
            if aw in self.awaiting:
                self.awaiting.remove(aw)
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
    "delete": "<path d='M4 7h16'/><path d='M10 11v6'/><path d='M14 11v6'/>"
              "<path d='M6 7l1 13h10l1-13'/><path d='M9 7V4h6v3'/>",
    "borrow": "<path d='M12 5v14'/><path d='M5 12h14'/>",
    "search": "<circle cx='11' cy='11' r='7'/><path d='m20 20-3.5-3.5'/>",
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
:root:not([data-theme=light]){
--bg:#161615;
--fg:#ecebe7;
--mut:#9b9a95;
--card:#1f1f1d;
--line:#33322f;
--acc:#6fbf8f;
--warn:#e0a040;
--err:#e07070}
}
:root[data-theme=dark]{
--bg:#161615;
--fg:#ecebe7;
--mut:#9b9a95;
--card:#1f1f1d;
--line:#33322f;
--acc:#6fbf8f;
--warn:#e0a040;
--err:#e07070}
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
input[type=text],input[type=email],input[type=password],input[type=number],select{
width:100%;
padding:8px;
border:1px solid var(--line);
border-radius:8px;
background:var(--bg);
color:var(--fg);
font:inherit}
.chk{
display:flex;
gap:8px;
align-items:center;
margin-top:12px;
color:var(--fg);
font-size:15px}
.hint{
display:block;
margin-top:4px;
font-size:12px;
color:var(--mut)}
.set h2{
font-size:15px;
color:var(--fg);
margin-bottom:4px}
.set label:first-of-type{
margin-top:0}
@media (max-width:600px){
.hide-sm{
display:none}
}
form.del{
margin:0}
form.del button{
padding:4px 6px;
color:var(--mut)}
form.del .ic{
width:18px;
height:18px}
form.del button:hover{
color:#c0392b;
border-color:#c0392b}
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
.cover{
width:44px;
border-radius:4px;
display:block}
.bk{
display:flex;
gap:10px;
align-items:flex-start}
.bk .cover{
flex:none}
.kind{
display:inline-flex}
.kind .ic{
width:22px;
height:22px}
.kind.audio{
color:var(--acc)}
.tag{
margin-left:6px;
font-size:12px;
color:var(--acc);
vertical-align:middle}
.actions select{
width:auto}
input[type=search]{
padding:8px;
border:1px solid var(--line);
border-radius:8px;
background:var(--bg);
color:var(--fg);
font:inherit}
summary{
cursor:pointer;
margin-top:10px}
header nav a{
padding:6px 2px;
color:var(--mut);
border-bottom:2px solid transparent}
header nav a:hover{
color:var(--fg)}
header nav a.on{
color:var(--acc);
border-bottom-color:var(--acc)}
.cardhead{
display:flex;
flex-wrap:wrap;
align-items:center;
gap:8px;
margin-bottom:8px}
.cardhead h2{
margin:0}
.cardhead form{
margin-left:auto}
.status{
margin:0 0 12px}
pre.logfull{
max-height:none}
td form{
margin:0}
.st-new{
color:var(--fg)}
th.sort button{
padding:0;
border:0;
background:none;
color:inherit;
font:inherit;
gap:4px}
th.sort button::after{
content:'↕';
opacity:.35}
th[aria-sort=ascending] button::after{
content:'▲';
opacity:1;
font-size:10px}
th[aria-sort=descending] button::after{
content:'▼';
opacity:1;
font-size:10px}
td.rt{
white-space:nowrap}
.tfilter{
align-items:center;
margin-bottom:8px}
.tfilter input{
flex:1;
min-width:160px}
.only-sm{
display:none}
@media (max-width:600px){
.only-sm{
display:block}
}
.rt b{
font-weight:600}
.theme{
padding:6px;
border:0;
background:none;
color:var(--mut)}
.theme:hover{
color:var(--fg)}
.theme .ic{
display:none}
:root:not([data-theme]) .theme .t-auto,
:root[data-theme=light] .theme .t-light,
:root[data-theme=dark] .theme .t-dark{
display:inline}
.seg{
display:inline-flex;
border:1px solid var(--line);
border-radius:8px;
overflow:hidden}
.seg label{
margin:0;
display:flex;
align-items:center;
padding:0 10px;
color:var(--mut);
cursor:pointer}
.seg label+label{
border-left:1px solid var(--line)}
.seg input{
position:absolute;
opacity:0;
width:1px;
height:1px}
.seg .ic{
width:20px;
height:20px}
.seg label:hover{
color:var(--fg)}
.seg input:checked+label{
background:var(--acc);
color:#fff}
.seg input:focus-visible+label{
outline:2px solid var(--acc);
outline-offset:-2px}
@media (max-width:600px){
header{
flex-wrap:wrap;
gap:8px 14px}
header nav{
order:3;
margin-left:0;
width:100%;
gap:18px}
.cardhead form{
margin-left:0}
.actions button span{
display:none}
}
"""


def esc(v):
    return html.escape(str(v if v is not None else ""))


def page(t, title, body, path="/", refresh=None):
    meta = "<meta http-equiv='refresh' content='%d'>" % refresh if refresh else ""
    return ("<!doctype html><html lang='%s'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>%s"
            "<title>%s</title><link rel='icon' type='image/png' href='/favicon.png'>"
            "<link rel='apple-touch-icon' href='/apple-touch-icon.png'>"
            "<style>%s</style><script>%s</script></head><body>"
            "<header><h1><img src='/icon.png' alt=''>Neknihy</h1><nav>%s</nav>"
            "<button type='button' class='theme' onclick='nkTheme()' %s>%s%s%s</button>"
            "</header><main>%s</main></body></html>"
            % (t.lang, meta, esc(title), CSS, THEME_JS, "".join(
                "<a href='%s'%s>%s%s</a>" % (
                    href, " class='on' aria-current='page'" if href == path else "",
                    icon(ic), esc(t(key)))
                for href, ic, key in TABS), " ".join(
                   "data-%s='%s: %s'" % (m, esc(t("theme.toggle")), esc(t("theme." + m)))
                   for m in THEMES),
               icon("auto", "ic t-auto"), icon("sun", "ic t-light"), icon("moon", "ic t-dark"),
               body))


# Theme cycle on the header button; "auto" follows the system setting.
THEMES = ("auto", "light", "dark")


# Applied in <head> so the saved theme is set before the first paint; the
# choice is per browser (localStorage "nk-theme"; the old "theme" key, saved
# by the first light/dark-only toggle, is ignored), "auto" (no data-theme) is
# the default.
# The button shows the current mode (CSS) and names it in its title.
THEME_JS = (
    "(function(){try{var v=localStorage.getItem('nk-theme');"
    "if(v=='light'||v=='dark')document.documentElement.setAttribute('data-theme',v)}"
    "catch(e){}})();"
    "function nkLabel(){var b=document.querySelector('.theme'),"
    "m=document.documentElement.getAttribute('data-theme')||'auto';"
    "b.title=b.getAttribute('data-'+m);b.setAttribute('aria-label',b.title)}"
    "document.addEventListener('DOMContentLoaded',nkLabel);"
    "function nkTheme(){var r=document.documentElement,c=r.getAttribute('data-theme')||'auto',"
    "n={auto:'light',light:'dark',dark:'auto'}[c];"
    "try{n=='auto'?localStorage.removeItem('nk-theme'):localStorage.setItem('nk-theme',n)}catch(e){}"
    "n=='auto'?r.removeAttribute('data-theme'):r.setAttribute('data-theme',n);nkLabel()}")


# Navigation tabs: (path, icon, label key)
TABS = (("/", "loans", "nav.loans"), ("/settings", "settings", "nav.settings"),
        ("/log", "log", "log.title"))


def fmt_date(t, value):
    try:
        return datetime.fromisoformat(value).strftime(t("fmt.date"))
    except Exception:
        return esc(value)


def sort_th(label, kind="text", cls="", first="asc"):
    """Header of a sortable column (kind "text" or "num"; see TABLE_JS)."""
    return ("<th class='sort%s' data-type='%s' data-first='%s'><button type='button'>%s"
            "</button></th>" % (" " + cls if cls else "", kind, first, esc(label)))


def kind_cell(t, audio):
    """Cell saying whether a title is an e-book or an audiobook."""
    label = t("kind.audio_tag" if audio else "kind.ebook_tag")
    return ("<td data-v='%s' title='%s' aria-label='%s'><span class='kind%s'>%s</span></td>"
            % (esc(label), esc(label), esc(label), " audio" if audio else "",
               icon("audio" if audio else "ebook")))


def filter_box(t, table_id):
    """Text box that hides the rows of a table that do not match (see TABLE_JS)."""
    return ("<div class='actions tfilter'><input type='search' data-for='%s' "
            "placeholder='%s' aria-label='%s' autocomplete='off'>"
            "<span class='mut' data-count='%s' hidden></span></div>" % (
                table_id, esc(t("filter.placeholder")), esc(t("filter.placeholder")),
                esc(t("filter.count", n="{n}", total="{total}"))))


def rating_attrs(t):
    """Texts the table script needs to show ratings it loads later."""
    return ("data-fmt='%s' data-none='%s' data-count='%s'" % (
        esc(t("rating.value", p="{p}")), esc(t("rating.none")), esc(t("rating.count", n="{n}"))))


def rating_cell(svc, t, web_id):
    """Reader rating in percent; unknown ones are loaded by TABLE_JS."""
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


# Sortable tables: click a header to sort (again to reverse); the choice is
# remembered per table, as the loans page reloads itself while busy. Cells
# carry their sort value in data-v. Ratings not cached yet (data-rid) are
# fetched from /ratings and filled in.
DELETE_JS = """<script>document.querySelectorAll('form.del').forEach(function(f){
f.addEventListener('submit',function(e){if(!confirm(f.dataset.confirm))e.preventDefault();});
});</script>"""

TABLE_JS = """<script>(function(){
function key(tb){return 'neknihy.sort.'+tb.id;}
function apply(tb,col,dir){var ths=tb.tHead.rows[0].cells,th=ths[col];
if(!th||!th.classList.contains('sort'))return;
for(var i=0;i<ths.length;i++)ths[i].removeAttribute('aria-sort');
th.setAttribute('aria-sort',dir>0?'ascending':'descending');
var num=th.getAttribute('data-type')==='num',body=tb.tBodies[0],
rows=Array.prototype.slice.call(body.rows),lang=document.documentElement.lang;
rows.sort(function(a,b){var x=a.cells[col].getAttribute('data-v')||'',
y=b.cells[col].getAttribute('data-v')||'',r=num?(Number(x)-Number(y)):
x.localeCompare(y,lang,{sensitivity:'base',numeric:true});
return r*dir||(a.getAttribute('data-i')-b.getAttribute('data-i'));});
rows.forEach(function(r){body.appendChild(r);});
try{localStorage.setItem(key(tb),col+':'+dir);}catch(e){}}
function current(tb){var ths=tb.tHead.rows[0].cells;for(var i=0;i<ths.length;i++){
var s=ths[i].getAttribute('aria-sort');if(s)return [i,s==='ascending'?1:-1];}return null;}
function norm(s){return s.normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').toLowerCase();}
function filter(tb,box){var words=norm(box.value).split(/\\s+/).filter(Boolean),n=0,
rows=tb.tBodies[0].rows,cnt=box.parentNode.querySelector('[data-count]');
Array.prototype.forEach.call(rows,function(r){var txt=norm(r.textContent),
ok=words.every(function(w){return txt.indexOf(w)>=0;});r.hidden=!ok;if(ok)n++;});
cnt.hidden=!words.length;cnt.textContent=cnt.getAttribute('data-count')
.replace('{n}',n).replace('{total}',rows.length);
try{sessionStorage.setItem('neknihy.filter.'+tb.id,box.value);}catch(e){}}
document.querySelectorAll('table.sortable').forEach(function(tb){
Array.prototype.forEach.call(tb.tBodies[0].rows,function(r,i){r.setAttribute('data-i',i);});
var box=document.querySelector('input[data-for="'+tb.id+'"]');
if(box){try{box.value=sessionStorage.getItem('neknihy.filter.'+tb.id)||'';}catch(e){}
box.addEventListener('input',function(){filter(tb,box);});if(box.value)filter(tb,box);}
Array.prototype.forEach.call(tb.tHead.rows[0].cells,function(th,col){
if(!th.classList.contains('sort'))return;
th.querySelector('button').addEventListener('click',function(){var c=current(tb);
apply(tb,col,c&&c[0]===col?-c[1]:(th.getAttribute('data-first')==='desc'?-1:1));});});
try{var s=(localStorage.getItem(key(tb))||'').split(':');
if(s.length===2)apply(tb,+s[0],+s[1]);}catch(e){}
var cells=tb.querySelectorAll('td[data-rid]'),ids=[];
cells.forEach(function(td){var id=td.getAttribute('data-rid');if(ids.indexOf(id)<0)ids.push(id);});
function fill(data,all){cells.forEach(function(td){var id=td.getAttribute('data-rid');
if(id===null||!(all||id in data))return;var r=data[id];td.removeAttribute('data-rid');
if(!r){td.textContent='–';return;}
if(r.percent===null){td.textContent='–';td.title=tb.getAttribute('data-none');return;}
td.classList.remove('mut');td.setAttribute('data-v',r.percent);
td.title=tb.getAttribute('data-count').replace('{n}',r.count);
var b=document.createElement('b');b.textContent=tb.getAttribute('data-fmt')
.replace('{p}',r.percent);td.textContent='';td.appendChild(b);});
var c=current(tb);if(c)apply(tb,c[0],c[1]);}
(function next(){if(!ids.length)return;var part=ids.splice(0,16);
fetch('/ratings?ids='+part.join(','),{credentials:'same-origin'}).then(function(r){
if(!r.ok)throw r;return r.json();}).then(function(d){fill(d,false);next();})
.catch(function(){fill({},true);});})();});})();</script>"""


def search_form(t, query="", kind="ebook"):
    icons = {"ebook": "ebook", "audiobook": "audio", "all": "both"}
    options = "".join(
        "<input type='radio' name='kind' value='%s' id='kind-%s'%s>"
        "<label for='kind-%s' title='%s' aria-label='%s'>%s</label>" % (
            k, k, " checked" if k == kind else "", k, esc(t("kind." + k)),
            esc(t("kind." + k)), icon(icons[k])) for k in katalog.KINDS)
    return ("<form method='get' action='/search' class='actions'>"
            "<input type='search' name='q' value='%s' required style='flex:1;min-width:200px' "
            "placeholder='%s' aria-label='%s'>"
            "<span class='seg' role='radiogroup' aria-label='%s'>%s</span>"
            "<button class='primary'>%s<span>%s</span></button></form>" % (
                esc(query), esc(t("search.placeholder")), esc(t("search.placeholder")),
                esc(t("kind.label")), options, icon("search"), esc(t("btn.search"))))


def not_configured_hint(t):
    link = "<a href='/settings'>%s</a>" % esc(t("settings_link"))
    return "<p class='mut'>%s</p>" % esc(
        t("borrow.not_configured", settings="\x00")).replace("\x00", link)


def render_borrow(svc, t):
    parts = [search_form(t)]
    if svc.app.settings.katalog_configured():
        parts.append(
            "<details><summary class='mut'>%s</summary>"
            "<form method='post' action='/borrow' class='actions' style='margin-top:8px'>"
            "<input type='text' name='work' required style='flex:1;min-width:200px' "
            "placeholder='https://katalog.tsk.sk/…?workId=1439847' aria-label='%s'>"
            "<button>%s<span>%s</span></button></form></details>" % (
                esc(t("borrow.label")), esc(t("borrow.label")), icon("borrow"),
                esc(t("btn.borrow"))))
    else:
        parts.append(not_configured_hint(t))
    return "<div class='card'><h2>%s%s</h2>%s</div>" % (
        icon("search"), esc(t("borrow.title")), "".join(parts))


def render_search(svc, t, query, page_no, kind="ebook"):
    app = svc.app
    s = app.settings
    body = ["<div class='card'><h2>%s%s</h2>%s</div>" % (
        icon("search"), esc(t("borrow.title")), search_form(t, query, kind))]
    try:
        res = katalog.search(query, library=s.tsk_library or "vkmr", page=page_no,
                             kind=kind)
    except Exception as e:
        log.error("Catalogue search failed: %s", e)
        body.append("<div class='card'><p class='st st-pending'>%s%s</p></div>" % (
            icon("warn"), esc(t("search.error", error=e))))
        return page(t, "%s – Neknihy" % t("btn.search"), "".join(body), "/")

    active = [b for b in app.books if b.get("neknihy", {}).get("status") != "expired"]
    # the catalogue links to palmknihy.cz/kniha/<id>, which is often not the
    # loan's palm_id (see catalogueIds); audiobooks are matched by title too
    loaned = set().union(*(catalogueIds(b) for b in active))
    loaned_audio = {(b.get("title") or "").strip().lower() for b in active if isAudiobook(b)}
    can_borrow = s.katalog_configured()
    waiting = {aw["work_id"] for aw in svc.awaiting}
    lib_url = "%s/library/%s" % (katalog.BASE_URL, esc(s.tsk_library or "vkmr"))
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
            "<tr><td class='hide-sm'>%s</td><td data-v='%s'><a href='%s/detail/%d' "
            "target='_blank' rel='noopener'>%s</a><div class='mut'>%s</div></td>%s"
            "<td class='hide-sm' data-v='%s'>%s</td>%s<td>%s</td></tr>" % (
                cover, esc(r["title"]), lib_url, r["work_id"],
                esc(r["title"] or r["work_id"]), esc(r["publish_info"]),
                kind_cell(t, r.get("audiobook")),
                esc(authors), esc(authors), rating_cell(svc, t, r["palm_id"]), action))
    if rows:
        table = ("<p class='mut'>%s</p>%s<table class='sortable' id='search' %s><thead><tr>"
                 "<th class='hide-sm'></th>%s%s%s%s<th></th></tr></thead><tbody>%s</tbody>"
                 "</table>%s" % (
                     esc(t("search.results_" + res["kind"], n=res["total"])),
                     filter_box(t, "search"), rating_attrs(t),
                     sort_th(t("th.book")), sort_th(t("th.type")),
                     sort_th(t("th.author"), cls="hide-sm"),
                     sort_th(t("th.rating"), "num", first="desc"), "".join(rows), TABLE_JS))
    else:
        table = "<p class='mut empty'>%s%s</p>" % (icon("empty"), esc(t("search.none")))
    if res["pages"] > 1:
        links = []
        for n in range(1, res["pages"] + 1):
            links.append("<b>%d</b>" % n if n == page_no else
                         "<a href='/search?q=%s&amp;kind=%s&amp;page=%d'>%d</a>"
                         % (quote(query), quote(res["kind"]), n, n))
        table += "<p class='actions'>%s</p>" % " ".join(links)
    if not can_borrow:
        table += not_configured_hint(t)
    body.append("<div class='card'>%s</div>" % table)
    return page(t, "%s – Neknihy" % t("btn.search"), "".join(body), "/")


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
        delete = ("<td><form method='post' action='/delete' class='del' "
                  "data-confirm='%s'><input type='hidden' name='palm_id' value='%s'>"
                  "<input type='hidden' name='audio' value='%d'>"
                  "<button title='%s' aria-label='%s'>%s</button></form></td>" % (
                      esc(t("delete.confirm", title=title)), esc(b.get("palm_id", "")),
                      isAudiobook(b), esc(t("btn.delete")), esc(t("btn.delete")),
                      icon("delete")))
        rows.append(
            "<tr><td data-v='%s'><div class='bk'>%s<div>%s<div class='mut only-sm'>%s</div>"
            "</div></div></td>%s<td class='hide-sm' data-v='%s'>%s</td>%s"
            "<td class='hide-sm mut' data-v='%s'>%s</td>"
            "<td class='hide-sm' data-v='%s'>%s</td>"
            "<td data-v='%d'><span class='st st-%s'>%s%s</span></td>%s</tr>" % (
                esc(title), cover, esc(title), esc(authors), kind_cell(t, isAudiobook(b)),
                esc(authors), esc(authors), rating_cell(svc, t, webId(b)),
                esc(filename), esc(filename),
                esc(b.get("end_time") or ""), fmt_date(t, b.get("end_time", "")),
                STATUSES.index(st) if st in STATUSES else len(STATUSES), esc(st),
                icon(st) if st in STATUSES else "", esc(label), delete))
    table = ("%s<table class='sortable' id='loans' %s><thead><tr>%s%s%s%s%s%s%s<th></th></tr>"
             "</thead><tbody>%s</tbody></table>%s%s" % (
                 filter_box(t, "loans"), rating_attrs(t), sort_th(t("th.book")),
                 sort_th(t("th.type")), sort_th(t("th.author"), cls="hide-sm"),
                 sort_th(t("th.rating"), "num", first="desc"),
                 sort_th(t("th.file"), cls="hide-sm"),
                 sort_th(t("th.due"), cls="hide-sm"), sort_th(t("th.status")),
                 "".join(rows), TABLE_JS, DELETE_JS)
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
    # search on top, then the loans with the actions that work on them
    body = ("%s%s<div class='card'><div class='cardhead'><h2>%s%s</h2>"
            "<form method='post' action='/action' class='actions'>%s</form></div>"
            "<p class='mut status'>%s</p>%s</div>" % (
                awaiting, render_borrow(svc, t), icon("loans"), esc(t("nav.loans")),
                buttons, "<br>".join(status), table))
    busy = svc.running or svc.awaiting
    return page(t, "Neknihy", body, "/", refresh=PAGE_REFRESH if busy else None)


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
    body = ("<div class='card'><h2>%s%s</h2><p class='mut status'>%s</p>%s</div>" % (
        icon("log"), esc(t("log.title")), "<br>".join(run_status(svc, t)),
        "<pre class='logfull'>%s</pre>" % esc(lines) if lines else
        "<p class='mut empty'>%s%s</p>" % (icon("empty"), esc(t("empty")))))
    busy = svc.running or svc.awaiting
    return page(t, "%s – Neknihy" % t("log.title"), body, "/log",
                refresh=PAGE_REFRESH if busy else None)


def language_select(t, choice):
    """<select> with 'auto' (browser) plus every available language."""
    opts = [("auto", t("set.lang_auto"))] + i18n.available()
    return "<select name='lang' id='lang'>%s</select>" % "".join(
        "<option value='%s'%s>%s</option>" % (
            esc(code), " selected" if code == choice else "", esc(name))
        for code, name in opts)


def library_select(choice):
    """<select> of the catalogues on katalog.tsk.sk; keeps an unknown code."""
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
        section("ebook", "set.sec_folders", folders),
        section("pending", "set.sec_auto", auto),
        section("loans", "set.tsk", tsk),
        section("refresh", "set.sec_convert", convert),
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

        def _send_json(self, obj):
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
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

        def _lang_cookie(self):
            """Language stored in this browser: a code, 'auto' or None."""
            raw = self.headers.get("Cookie")
            if raw:
                try:
                    c = SimpleCookie(raw)
                    if "neknihy_lang" in c:
                        v = c["neknihy_lang"].value
                        return "auto" if v == "auto" else i18n.normalize(v)
                except Exception:
                    pass
            return None

        def _translator(self):
            choice = self._lang_cookie()
            browser = i18n.from_accept_language(self.headers.get("Accept-Language"))
            if choice == "auto":  # explicitly "follow the browser"
                lang = browser or DEFAULT_LANG or FALLBACK_LANG
            else:
                lang = choice or DEFAULT_LANG or browser or FALLBACK_LANG
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
            elif path == "/log":
                self._send(render_log(svc, t))
            elif path == "/settings":
                choice = self._lang_cookie() or ("auto" if not DEFAULT_LANG else DEFAULT_LANG)
                self._send(render_settings(svc, t, choice))
            elif path == "/search":
                q = parse_qs(url.query)
                query = q.get("q", [""])[0].strip()
                try:
                    page_no = max(1, int(q.get("page", ["1"])[0]))
                except ValueError:
                    page_no = 1
                kind = q.get("kind", ["ebook"])[0]
                if kind not in katalog.KINDS:
                    kind = "ebook"
                if query:
                    self._send(render_search(svc, t, query[:200], page_no, kind))
                else:
                    self._redirect("/")
            elif path == "/ratings":
                raw = parse_qs(url.query).get("ids", [""])[0]
                ids = [int(i) for i in raw.split(",") if i.strip().isdigit()][:MAX_IDS]
                data = svc.ratings.fetch(ids) if ids else {}
                self._send_json({str(k): v for k, v in data.items()})
            elif path == "/lang":
                q = parse_qs(url.query)
                raw = q.get("l", [""])[0]
                lang = "auto" if raw == "auto" else i18n.normalize(raw)
                nxt = q.get("next", ["/"])[0]
                if nxt not in ("/", "/settings", "/log"):
                    nxt = "/"  # no open redirects
                cookie = (LANG_COOKIE % lang) if lang else None
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
                raw = form.get("lang", "")
                lang = "auto" if raw == "auto" else i18n.normalize(raw)
                cookie = (LANG_COOKIE % lang) if lang else None
                self._redirect("/", cookie)
            elif path == "/action":
                a = form.get("a", "")
                if a in ("run", "refresh", "download", "sync", "return"):
                    threading.Thread(target=svc.action, args=(a,), daemon=True).start()
                    time.sleep(0.3)
                self._redirect("/")
            elif path == "/delete":
                if form.get("palm_id"):
                    svc.delete((form.get("audio") == "1", form["palm_id"]))
                self._redirect("/")
            elif path == "/borrow":
                work_id = parse_work_id(form.get("work", ""))
                if work_id is None:
                    log.error("Not a catalogue link or work ID: %s", form.get("work", ""))
                else:
                    try:
                        palm_id = int(form.get("palm_id", ""))
                    except ValueError:
                        palm_id = None
                    title = form.get("title", "").strip()[:300]
                    threading.Thread(target=svc.borrow, args=(work_id, palm_id, title),
                                     daemon=True).start()
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
