import os
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
