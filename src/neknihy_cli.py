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
    NEKNIHY_INGEST     "1" = READERDIR is an ingest folder (Calibre-Web
                       Automated / NextGen): copy every book there only once,
                       because the ingest process imports and deletes it
    NEKNIHY_DEBUG      "1" = print HTTP debug output

Search the library catalogue (no login): neknihy_cli.py --search QUERY
    NEKNIHY_TSK_USERNAME  library catalogue (katalog.tsk.sk) login, for --borrow
    NEKNIHY_TSK_PASSWORD  library catalogue password
    NEKNIHY_TSK_LIBRARY   library code in the catalogue URL (default vkmr)
"""

import argparse
import logging
import os
import shutil
import signal
import sys
import threading
from datetime import datetime

from neknihy.app import App
from neknihy import katalog
from neknihy.katalog import parse_work_id

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
    s.tsk_username = env("NEKNIHY_TSK_USERNAME")
    s.tsk_password = env("NEKNIHY_TSK_PASSWORD")
    s.tsk_library = env("NEKNIHY_TSK_LIBRARY", "vkmr")

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


def sync_ingest(app):
    """Copy each downloaded book into the ingest folder exactly once.

    Calibre-Web Automated / NextGen import files from the ingest folder and
    delete them, so the normal sync (copy if missing) would import the same
    book again on every run. Copied books are marked in .data instead.
    The file is written directly (no temporary name) so the ingest watcher
    sees only the finished file (close_write).
    """
    dst_dir = app.settings.readerdir
    if not dst_dir or not os.path.isdir(dst_dir):
        log.warning("Ingest folder not set or not found: %s", dst_dir)
        return None
    added = []
    for i, book in enumerate(app.books):
        nk = book.get("neknihy", {})
        if nk.get("status") != "ok" or nk.get("ingested"):
            continue
        src = app.bookFile(i)
        if not src or not os.path.exists(src):
            continue
        shutil.copyfile(src, os.path.join(dst_dir, nk["filename"]))
        nk["ingested"] = datetime.now().isoformat(timespec="seconds")
        added.append(nk["filename"])
    app.saveBooks()
    return added


def sync(app, ingest):
    if not app.settings.readerdir:
        return
    if ingest:
        added = sync_ingest(app)
        if added is not None:
            log.info("Ingest to %s: added %d%s", app.settings.readerdir, len(added),
                     (" (" + ", ".join(added) + ")") if added else "")
        return
    result = app.syncReader()
    if result is None:
        log.warning("Sync folder not found: %s", app.settings.readerdir)
    else:
        log.info("Sync to %s: added %d, removed %d, total %d",
                 app.settings.readerdir, len(result["added"]),
                 len(result["removed"]), result["total"])


def run_once(app, auto_return=None, ingest=None):
    if auto_return is None:
        auto_return = env("NEKNIHY_AUTO_RETURN", "1") == "1"
    if ingest is None:
        ingest = env("NEKNIHY_INGEST", "0") == "1"
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

    if auto_return:
        expired = [b["neknihy"]["filename"] for b in app.books
                   if b["neknihy"]["status"] == "expired"]
        if expired:
            log.info("Removing expired loans: %s", ", ".join(f for f in expired if f))
        app.returnBooks()

    sync(app, ingest)


def print_search(query):
    """Print catalogue search results; returns the exit code."""
    try:
        res = katalog.search(query, library=env("NEKNIHY_TSK_LIBRARY", "vkmr"))
    except Exception as e:
        log.error("Search failed: %s", e)
        return 1
    for r in res["results"]:
        print("%-9d %s – %s (%s)" % (r["work_id"], r["title"], "; ".join(r["authors"]),
                                     r["publish_info"]))
    print("%d e-book(s). Borrow one with: --borrow WORK_ID" % res["total"])
    return 0


def main():
    parser = argparse.ArgumentParser(description="Neknihy headless runner")
    parser.add_argument("--once", action="store_true", help="run once and exit")
    parser.add_argument("--workdir", help="override NEKNIHY_WORKDIR")
    parser.add_argument("--readerdir", help="override NEKNIHY_READERDIR")
    parser.add_argument("--borrow", metavar="WORK",
                        help="borrow an e-book in katalog.tsk.sk (link or work ID), "
                             "download it and exit")
    parser.add_argument("--search", metavar="QUERY",
                        help="search e-books in katalog.tsk.sk, print them and exit "
                             "(no login needed)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")

    def handle_signal(signum, frame):
        log.info("Stopping")
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    if args.search:
        sys.exit(print_search(args.search))

    app = build_app(args)
    if args.borrow:
        work_id = parse_work_id(args.borrow)
        if work_id is None:
            log.error("Not a catalogue link or work ID: %s", args.borrow)
            sys.exit(2)
        try:
            app.borrowFromKatalog(work_id)
        except Exception as e:
            log.error("Borrow failed: %s", e)
            sys.exit(1)
        args.once = True
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
