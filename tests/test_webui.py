import os
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from neknihy import i18n  # noqa: E402
from neknihy.app import App  # noqa: E402
from neknihy import webui  # noqa: E402

SAMPLE_OK = {"palm_id": 101, "title": "Vražda v Orient exprese", "item_type": "ebook",
             "end_time": "2099-11-25T10:00:00+00:00",
             "neknihy": {"status": "ok", "filename": "Vrazda.epub",
                         "authors": ["Agatha Christie"]}}


class FakeRatings:
    def get(self, web_id):
        return None


def make_svc(books=(), configured=True, katalog=True, running=False,
             awaiting=(), last_run=None, last_error=None, next_run=None):
    """Fake Service with just what the renderers read."""
    settings = SimpleNamespace(
        email="a@b.c", password="x", workdir="/books", readerdir="", audiodir="",
        convert="0", convertor="ebook-convert", tsk_library="vkmr",
        tsk_username="u" if katalog else "", tsk_password="p" if katalog else "")
    settings.configured = lambda: configured
    settings.katalog_configured = lambda: katalog
    app = SimpleNamespace(books=list(books), settings=settings)
    app.bookAuthors = lambda b: ", ".join((b.get("neknihy") or {}).get("authors") or [])
    app.dateFromString = lambda d: App.dateFromString(app, d)
    return SimpleNamespace(
        app=app, ratings=FakeRatings(), running=running, awaiting=list(awaiting),
        last_run=last_run, last_error=last_error, next_run=next_run,
        web={"interval": 3600, "auto_return": True, "ingest": False},
        log=SimpleNamespace(lines=["line one"]))


T = i18n.Translator("sk")


class Pages(unittest.TestCase):
    def test_pages_render(self):
        svc = make_svc(books=[SAMPLE_OK])
        for html in (webui.render_index(svc, T), webui.render_log(svc, T),
                     webui.render_settings(svc, T)):
            self.assertIn("<link rel='stylesheet' href='/app.css?v=", html)
            self.assertIn("<script src='/app.js?v=", html)

    def test_static_files_registered(self):
        self.assertEqual(webui.STATIC["/app.css"], "app.css")
        self.assertEqual(webui.STATIC["/app.js"], "app.js")
        for name in ("app.css", "app.js"):
            self.assertTrue(os.path.isfile(os.path.join(webui.STATIC_DIR, name)))


NOW = datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc)
PARSE = make_svc().app.dateFromString


def loan(pid, title, status="ok", end=None, audio=False, **extra):
    b = {"palm_id": pid, "title": title, "item_type": "audiobook" if audio else "ebook",
         "neknihy": {"status": status, "filename": "", "authors": ["Autor"]}}
    if end is not None:
        b["end_time"] = end
    b.update(extra)
    return b


class Helpers(unittest.TestCase):
    def test_days_left(self):
        cases = {"2026-11-11T23:00:00+00:00": 0, "2026-11-12T01:00:00+00:00": 1,
                 "2026-11-25T10:00:00+00:00": 14, "": None, "nonsense": None}
        for end, want in cases.items():
            with self.subTest(end=end):
                self.assertEqual(webui.days_left(end, NOW, PARSE), want)
        self.assertIsNone(webui.days_left(None, NOW, PARSE))

    def test_due_text_sk(self):
        cases = {None: "–", 0: "dnes", 1: "zajtra", 3: "3 dni", 7: "7 dní"}
        for days, want in cases.items():
            with self.subTest(days=days):
                self.assertEqual(webui.due_text(T, days), want)

    def test_is_soon(self):
        for days in (0, 3):
            self.assertTrue(webui.is_soon(days))
        for days in (4, -1, None):
            self.assertFalse(webui.is_soon(days))

    def test_split_loans(self):
        books = [loan(1, "ten", end="2026-11-21T10:00:00+00:00"),
                 loan(2, "two", end="2026-11-13T10:00:00+00:00"),
                 loan(3, "none"),
                 loan(4, "old", "expired", end="2026-11-01T10:00:00+00:00"),
                 loan(5, "gone", "deleted", end="2026-11-30T10:00:00+00:00")]
        active, expired = webui.split_loans(books, NOW, PARSE)
        self.assertEqual([b["title"] for b in active], ["two", "ten", "none"])
        self.assertEqual([b["title"] for b in expired], ["old"])

    def test_status_key(self):
        self.assertEqual(webui.status_key(loan(1, "a", "pending")), "pending")
        self.assertEqual(webui.status_key(loan(1, "a", "weird")), "other")

    def test_loan_key_distinguishes_audio(self):
        self.assertEqual(webui.loan_key(loan(7, "e")), "7-e")
        self.assertEqual(webui.loan_key(loan(7, "a", audio=True)), "7-a")


if __name__ == "__main__":
    unittest.main()
