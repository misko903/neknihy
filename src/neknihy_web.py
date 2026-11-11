#!/usr/bin/env python3
"""Neknihy NextGen web interface + scheduler (no extra dependencies).

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
from urllib.parse import parse_qs, urlparse

from neknihy import i18n
from neknihy.api import catalogueIds, isAudiobook
from neknihy.app import App
from neknihy import katalog
from neknihy.katalog import parse_work_id
from neknihy.rating import MAX_IDS, Ratings
from neknihy_cli import env, run_once, sync
from neknihy.webui import (AUDIOBOOKSHELF_DIRS, STATIC, STATIC_DIR, WAIT_STEP, esc, page,
                           render_index, render_log, render_search, render_settings)

log = logging.getLogger("neknihy")

CONFIG_DIR = env("NEKNIHY_CONFIG_DIR", "/config")
CONF_FILE = os.path.join(CONFIG_DIR, "neknihy.conf")
WEB_FILE = os.path.join(CONFIG_DIR, "web.json")
PORT = int(env("NEKNIHY_WEB_PORT", "8478"))
WEB_PASSWORD = env("NEKNIHY_WEB_PASSWORD")
DEFAULT_LANG = i18n.normalize(env("NEKNIHY_LANG"))
FALLBACK_LANG = "cs"  # the app's original language
LANG_COOKIE = "neknihy_lang=%s; Path=/; Max-Age=31536000; SameSite=Lax"
WAIT_MAX = int(env("NEKNIHY_WAIT_MAX", "1800"))  # s to wait for a new loan

STATIC_TYPES = {".png": "image/png", ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8"}


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
            self.send_header("WWW-Authenticate", 'Basic realm="Neknihy NextGen"')
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
            self.send_header("Content-Type", STATIC_TYPES.get(
                os.path.splitext(name)[1], "application/octet-stream"))
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
            self._send(page(t, str(code), "<p>%s</p>" % esc(t(key)), svc=svc), code)

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
