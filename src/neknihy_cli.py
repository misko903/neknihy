#!/usr/bin/env python3
"""Headless (CLI / Docker) runner for Neknihy.

Configuration is read from environment variables so the app can run
without a GUI and without a config file:

    NEKNIHY_EMAIL      Palmknihy login e-mail (required)
    NEKNIHY_PASSWORD   Palmknihy password (required)
    NEKNIHY_WORKDIR    Folder for downloaded books (default /books)
    NEKNIHY_READERDIR  Optional second folder to sync books into
                       (e-reader, Calibre-Web ingest folder, ...)
    NEKNIHY_CONVERT    "1" = convert .epub to .mobi when syncing (needs Calibre)
    NEKNIHY_CONVERTOR  Path to ebook-convert (default ebook-convert)
    NEKNIHY_INTERVAL   Seconds between runs (default 21600 = 6 h); 0 = run once
    NEKNIHY_AUTO_RETURN "1" = delete files of expired loans (default 1)
    NEKNIHY_DEBUG      "1" = print HTTP debug output
"""

import argparse
import logging
import os
import shutil
import signal
import sys
import threading

from neknihy.app import App

log = logging.getLogger("neknihy")
stop_event = threading.Event()


def env(name, default=""):
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def build_app(args):
    debug = env("NEKNIHY_DEBUG", "0") == "1"
    # use a non-existing config path so no GUI config is read or written
    app = App(config="/nonexistent/neknihy.conf", debug=debug)
    s = app.settings
    s.email = env("NEKNIHY_EMAIL")
    s.password = env("NEKNIHY_PASSWORD")
    s.workdir = args.workdir or env("NEKNIHY_WORKDIR", "/books")
    s.readerdir = args.readerdir or env("NEKNIHY_READERDIR")
    s.convert = env("NEKNIHY_CONVERT", "0")
    s.convertor = env("NEKNIHY_CONVERTOR", "ebook-convert")

    if not s.email or not s.password:
        log.error("NEKNIHY_EMAIL and NEKNIHY_PASSWORD must be set")
        sys.exit(2)
    os.makedirs(s.workdir, exist_ok=True)
    if s.readerdir:
        os.makedirs(s.readerdir, exist_ok=True)
    if s.convert == "1" and shutil.which(s.convertor) is None:
        log.warning("ebook-convert not found (%s), mobi conversion disabled", s.convertor)
        s.convert = "0"
    app.loadBooks()
    return app


def run_once(app):
    log.info("Refreshing list of rented books")
    app.refreshRents()
    active = [b for b in app.books if b["neknihy"]["status"] != "expired"]
    log.info("Active loans: %d", len(active))

    log.info("Downloading new books")
    try:
        app.downloadBooks()
    except Exception as e:
        log.error("Some downloads failed: %s", e)
    for b in app.books:
        status = b["neknihy"]["status"]
        title = b.get("title", b.get("palm_id"))
        if status == "pending":
            log.info("  pending (not yet available): %s", title)
        elif status == "ok":
            log.info("  ok: %s", b["neknihy"]["filename"])

    if env("NEKNIHY_AUTO_RETURN", "1") == "1":
        expired = [b["neknihy"]["filename"] for b in app.books
                   if b["neknihy"]["status"] == "expired"]
        if expired:
            log.info("Removing expired loans: %s", ", ".join(f for f in expired if f))
        app.returnBooks()

    if app.settings.readerdir:
        result = app.syncReader()
        if result is not None:
            log.info("Sync to %s: added %d, removed %d, total %d",
                     app.settings.readerdir, len(result["added"]),
                     len(result["removed"]), result["total"])


def main():
    parser = argparse.ArgumentParser(description="Neknihy headless runner")
    parser.add_argument("--once", action="store_true", help="run once and exit")
    parser.add_argument("--workdir", help="override NEKNIHY_WORKDIR")
    parser.add_argument("--readerdir", help="override NEKNIHY_READERDIR")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")

    def handle_signal(signum, frame):
        log.info("Stopping")
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    app = build_app(args)
    interval = 0 if args.once else int(env("NEKNIHY_INTERVAL", "21600"))

    while not stop_event.is_set():
        try:
            run_once(app)
        except Exception as e:
            log.error("Run failed: %s", e)
            # force a fresh login next time (token may have expired)
            app.api.logout()
            if interval == 0:
                sys.exit(1)
        if interval == 0:
            break
        # drop cached login/rents so the next run starts fresh
        app.api.logout()
        log.info("Next run in %d s", interval)
        stop_event.wait(interval)


if __name__ == "__main__":
    main()
