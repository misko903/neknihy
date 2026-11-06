#!/usr/bin/env python3
"""Neknihy web interface + scheduler (no extra dependencies).

    NEKNIHY_WEB_PORT      port to listen on (default 8478)
    NEKNIHY_WEB_PASSWORD  optional password for the web UI (HTTP basic auth,
                          any user name)
    NEKNIHY_CONFIG_DIR    folder for settings (default /config)

Settings entered in the UI are stored in NEKNIHY_CONFIG_DIR. On first start
NEKNIHY_EMAIL / NEKNIHY_PASSWORD / NEKNIHY_WORKDIR / NEKNIHY_READERDIR /
NEKNIHY_INTERVAL / NEKNIHY_AUTO_RETURN env vars are used as initial values.
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
from urllib.parse import parse_qs, urlparse

from neknihy.app import App
from neknihy_cli import env, run_once

log = logging.getLogger("neknihy")

CONFIG_DIR = env("NEKNIHY_CONFIG_DIR", "/config")
CONF_FILE = os.path.join(CONFIG_DIR, "neknihy.conf")
WEB_FILE = os.path.join(CONFIG_DIR, "web.json")
PORT = int(env("NEKNIHY_WEB_PORT", "8478"))
WEB_PASSWORD = env("NEKNIHY_WEB_PASSWORD")

STATUS_LABELS = {
    "new": "nová",
    "pending": "čeká na zpřístupnění",
    "ok": "stažená",
    "expired": "prošlá",
}


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
        self.last_result = ""
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
            self.last_result = "OK"
        except Exception as e:
            log.error("%s failed: %s", name, e)
            self.last_result = "chyba: %s" % e
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
"""


def esc(v):
    return html.escape(str(v if v is not None else ""))


def page(title, body):
    return ("<!doctype html><html lang='cs'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>%s</title><style>%s</style></head><body>"
            "<header><h1>Neknihy</h1><nav><a href='/'>Výpůjčky</a>"
            "<a href='/settings'>Nastavení</a></nav></header><main>%s</main></body></html>"
            % (esc(title), CSS, body))


def fmt_date(value):
    try:
        return datetime.fromisoformat(value).strftime("%d.%m.%Y")
    except Exception:
        return esc(value)


def render_index(svc):
    app = svc.app
    rows = []
    for b in app.books:
        st = b.get("neknihy", {}).get("status", "")
        rows.append(
            "<tr><td>%s<div class='mut'>%s</div></td><td class='hide-sm'>%s</td>"
            "<td class='st-%s'>%s</td><td class='hide-sm mut'>%s</td></tr>" % (
                esc(b.get("title", b.get("palm_id"))), esc(app.bookAuthors(b)),
                fmt_date(b.get("end_time", "")), esc(st), esc(STATUS_LABELS.get(st, st)),
                esc(b.get("neknihy", {}).get("filename", ""))))
    table = ("<table><tr><th>Kniha</th><th class='hide-sm'>Vrátit do</th><th>Stav</th>"
             "<th class='hide-sm'>Soubor</th></tr>%s</table>" % "".join(rows)
             if rows else "<p class='mut'>Žádné výpůjčky. Zkuste „Obnovit“.</p>")
    status = []
    if not app.settings.configured():
        status.append("<b>Není nastaveno</b> – vyplňte <a href='/settings'>nastavení</a>.")
    if svc.running:
        status.append("Probíhá operace…")
    if svc.last_run:
        last = svc.last_run.strftime("%d.%m. %H:%M")
        status.append("Poslední běh: %s (%s)" % (last, esc(svc.last_result)))
    if svc.next_run and svc.web["interval"] > 0:
        status.append("Další běh: %s" % datetime.fromtimestamp(svc.next_run).strftime(
            "%d.%m. %H:%M"))
    buttons = "".join(
        "<button name='a' value='%s'%s>%s</button>"
        % (a, " class='primary'" if a == "run" else "", label)
        for a, label in (("run", "Spustit vše"), ("refresh", "Obnovit"),
                         ("download", "Stáhnout"), ("sync", "Synchronizovat"),
                         ("return", "Smazat prošlé")))
    body = ("<div class='card'><form method='post' action='/action' class='actions'>%s</form>"
            "<p class='mut'>%s</p></div><div class='card'>%s</div>"
            "<div class='card'><pre>%s</pre></div>" % (
                buttons, "<br>".join(status), table,
                esc("\n".join(reversed(svc.log.lines)))))
    return page("Neknihy", body)


def render_settings(svc):
    s = svc.app.settings
    body = """<div class='card'><form method='post' action='/settings'>
<label>E-mail (Palmknihy)</label><input type='email' name='email' value='%s' required>
<label>Heslo %s</label><input type='password' name='password' autocomplete='new-password'>
<label>Složka pro knihy (v kontejneru)</label><input type='text' name='workdir' value='%s'>
<label>Složka pro synchronizaci (čtečka, ingest Calibre-Web; prázdné = vypnuto)</label>
<input type='text' name='readerdir' value='%s'>
<label>Interval automatického běhu (hodiny, 0 = vypnuto)</label>
<input type='number' min='0' name='interval_h' value='%d'>
<div class='chk'><input type='checkbox' name='auto_return' id='ar'%s>
<label for='ar' style='margin:0'>Automaticky mazat prošlé výpůjčky</label></div>
<div class='chk'><input type='checkbox' name='convert' id='cv'%s>
<label for='cv' style='margin:0'>Převádět na .mobi (vyžaduje Calibre)</label></div>
<label>Cesta k ebook-convert</label><input type='text' name='convertor' value='%s'>
<p><button class='primary'>Uložit</button></p></form></div>""" % (
        esc(s.email), "(ponechte prázdné pro zachování)" if s.password else "",
        esc(s.workdir), esc(s.readerdir), svc.web["interval"] // 3600,
        " checked" if svc.web["auto_return"] else "",
        " checked" if s.convert == "1" else "", esc(s.convertor))
    return page("Nastavení – Neknihy", body)


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

        def _redirect(self, to):
            self.send_response(303)
            self.send_header("Location", to)
            self.end_headers()

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/health":
                self._send("ok")
                return
            if not self._authorized():
                return
            if path == "/":
                self._send(render_index(svc))
            elif path == "/settings":
                self._send(render_settings(svc))
            else:
                self._send(page("404", "<p>Nenalezeno</p>"), 404)

        def do_POST(self):
            if not self._authorized():
                return
            if not self._same_origin():
                self._send(page("403", "<p>Zakázáno</p>"), 403)
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
            else:
                self._send(page("404", "<p>Nenalezeno</p>"), 404)

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
