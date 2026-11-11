import os
import re
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

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


def between(html, start, end):
    """The part of html from the first ``start`` to the next ``end``."""
    i = html.index(start)
    return html[i:html.index(end, i)]


class Sidebar(unittest.TestCase):
    def test_sidebar_nav_marks_current(self):
        html = webui.render_settings(make_svc(), T)
        self.assertEqual(html.count("aria-current='page'"), 1)
        self.assertIn("href='/settings' aria-current='page'", html)

    def test_sidebar_one_sync_button(self):
        html = webui.render_log(make_svc(), T)
        self.assertEqual(html.count("value='run'"), 1)
        self.assertIn("value='run'", between(html, "class='sync'", "</aside>"))
        self.assertNotIn("value='refresh'", html)

    def test_sidebar_not_configured(self):
        html = webui.render_log(make_svc(configured=False), T)
        self.assertNotIn("value='run'", html)
        self.assertIn("href='/settings'", between(html, "class='sync'", "</aside>"))

    def test_sidebar_error_truncated(self):
        err = "x" * 200 + "\nsecond"
        html = webui.render_log(make_svc(last_run=NOW, last_error=err), T)
        sync = between(html, "class='sync'", "</form>")
        self.assertIn(">" + "x" * 80 + "…<", sync)
        self.assertIn("title='" + "x" * 200, sync)
        visible = sync.replace("title='" + webui.esc(err) + "'", "")
        self.assertNotIn("second", visible)

    def test_loans_count(self):
        books = [loan(1, "a", end="2099-01-01T00:00:00+00:00"), loan(2, "b"),
                 loan(3, "c", "expired", end="2020-01-01T00:00:00+00:00")]
        html = webui.render_log(make_svc(books=books), T)
        self.assertIn("<span class='count'>2</span>", html)


def soon(days):
    from datetime import timedelta
    return (webui.local_now() + timedelta(days=days)).isoformat()


class Loans(unittest.TestCase):
    def render(self, books=(), **kw):
        return webui.render_index(make_svc(books=books, **kw), T)

    def test_expired_in_own_section(self):
        html = self.render([loan(1, "Aktivna", end=soon(5)),
                            loan(2, "Stara", "expired", end=soon(-3))])
        self.assertLess(html.index("Aktivna"), html.index("id='expired'"))
        self.assertGreater(html.index("Stara"), html.index("id='expired'"))

    def test_active_sorted_by_deadline(self):
        html = self.render([loan(1, "Desat", end=soon(10)), loan(2, "Dva", end=soon(2))])
        self.assertLess(html.index("Dva"), html.index("Desat"))

    def test_status_labels_sk(self):
        html = self.render([loan(1, "a", "pending", end=soon(5)),
                            loan(2, "b", "new", end=soon(6))])
        self.assertIn("Čaká na Palmknihy", html)
        self.assertIn("Stiahne sa pri synchronizácii", html)

    def test_soon_marked(self):
        html = self.render([loan(1, "Dva", end=soon(2)), loan(2, "Desat", end=soon(10))])
        self.assertEqual(html.count("class='due soon'"), 1)
        self.assertIn("class='due soon' data-v='2'", html)

    def test_actions_in_menu(self):
        html = self.render([loan(1, "a", end=soon(5))])
        menu = between(html, "<details class='menu'>", "</details>")
        for a in ("refresh", "download", "sync", "return"):
            with self.subTest(action=a):
                self.assertEqual(html.count("value='%s'" % a), 1)
                self.assertIn("value='%s'" % a, menu)

    def test_no_file_column(self):
        b = loan(1, "Duna", end=soon(5))
        b["neknihy"]["filename"] = "Duna.epub"
        html = self.render([b])
        self.assertEqual(html.count("Duna.epub"), 1)
        self.assertIn("data-file='Duna.epub'", html)

    def test_filter_only_over_8(self):
        books = [loan(i, "k%d" % i, end=soon(i)) for i in range(1, 9)]
        self.assertNotIn("data-for='loans'", self.render(books))
        books.append(loan(99, "k99", end=soon(30)))
        self.assertIn("data-for='loans'", self.render(books))

    def test_awaiting_first(self):
        html = self.render([loan(1, "Prva", end=soon(1))],
                           awaiting=[{"work_id": 5, "title": "Nova", "state": "waiting"}])
        self.assertIn("Požičiava sa…", html)
        self.assertLess(html.index("Nova"), html.index("Prva"))

    def test_escaping(self):
        evil = "<b>A'b\"&"
        b = loan(1, evil, end=soon(5))
        b["neknihy"]["authors"] = ["O'Brien"]
        html = self.render([b])
        self.assertNotIn("<b>A", html)
        self.assertIn("data-title='%s'" % webui.esc(evil), html)
        self.assertIn("data-authors='O&#x27;Brien'", html)

    def test_same_palm_id_audio_and_ebook(self):
        html = self.render([loan(7, "E", end=soon(5)), loan(7, "A", end=soon(5), audio=True)])
        self.assertIn("data-key='7-e'", html)
        self.assertIn("data-key='7-a'", html)

    def test_no_cover_placeholder(self):
        html = self.render([loan(1, "Bez obalu", end=soon(5))])
        self.assertIn("class='ph'", html)
        self.assertNotIn("<img src='http", html)

    def test_empty_state(self):
        html = self.render([])
        self.assertIn("href='/search'", between(html, "<main>", "</main>"))
        self.assertIn("Nájsť knihu", html)

    def test_no_deadline_last(self):
        html = self.render([loan(1, "Bez", None), loan(2, "S", end=soon(9))])
        self.assertLess(html.index(">S<"), html.index(">Bez<"))


class Detail(unittest.TestCase):
    def test_detail_template_present(self):
        html = webui.render_index(make_svc(books=[loan(1, "a", end=soon(5))]), T)
        self.assertIn("id='detail'", html)
        tpl = between(html, "<template id='detail-tpl'>", "</template>")
        self.assertIn("action='/delete'", tpl)
        self.assertIn("Otvoriť na Palmknihy", tpl)


def result(work_id, title, palm_id=None, audiobook=False, cover=""):
    return {"work_id": work_id, "title": title, "palm_id": palm_id, "authors": ["Autor"],
            "cover": cover, "publish_info": "Vydavateľ 2024", "ebook": True,
            "audiobook": audiobook}


def results(*items):
    return {"query": "q", "kind": "ebook", "page": 1, "pages": 1, "total": len(items),
            "results": list(items)}


class Search(unittest.TestCase):
    def test_search_empty_query_no_catalogue_call(self):
        with mock.patch.object(webui.katalog, "search") as search:
            html = webui.render_search(make_svc(), T, "", 1)
        search.assert_not_called()
        self.assertIn("class='bigsearch'", html)
        self.assertIn("E-knihy", between(html, "class='bigsearch'", "</form>"))

    def test_search_results_rows(self):
        books = [loan(1, "Mam", end=soon(5), cover="https://x/cover_555.jpg")]
        svc = make_svc(books=books)
        found = results(result(10, "Nova", palm_id=777), result(11, "Mam", palm_id=555))
        with mock.patch.object(webui.katalog, "search", return_value=found):
            html = webui.render_search(svc, T, "q", 1)
        self.assertIn("data-key='w10'", html)
        self.assertIn("data-action='borrow'", between(html, "data-key='w10'", ">"))
        self.assertIn("data-action='loaned'", between(html, "data-key='w11'", ">"))
        self.assertIn("name='work' value='10'", html)

    def test_search_kind_checked(self):
        with mock.patch.object(webui.katalog, "search", return_value=results()):
            html = webui.render_search(make_svc(), T, "q", 1, kind="audiobook")
        self.assertIn("value='audiobook' id='kind-audiobook' checked", html)

    def test_search_route_without_query(self):
        with mock.patch.object(webui.katalog, "search") as search:
            html = webui.search_page(make_svc(), T, "")
        search.assert_not_called()
        self.assertIn("class='bigsearch'", html)

    def test_search_route_parses_query(self):
        with mock.patch.object(webui.katalog, "search", return_value=results()) as search:
            webui.search_page(make_svc(), T, "q=dune&kind=all&page=2")
        self.assertEqual(search.call_args.args[0], "dune")
        self.assertEqual(search.call_args.kwargs["kind"], "all")
        self.assertEqual(search.call_args.kwargs["page"], 2)

    def test_search_detail_template(self):
        with mock.patch.object(webui.katalog, "search", return_value=results(result(1, "a"))):
            html = webui.render_search(make_svc(), T, "q", 1)
        tpl = between(html, "<template id='search-tpl'>", "</template>")
        self.assertIn("action='/borrow'", tpl)
        self.assertIn("data-eq='action:borrow'", tpl)


class SettingsAndLog(unittest.TestCase):
    def test_settings_section_anchors(self):
        html = webui.render_settings(make_svc(), T)
        links = re.findall(r"href='#(set-[a-z]+)'", html)
        self.assertEqual(len(links), 6)
        for anchor in links:
            self.assertIn("id='%s'" % anchor, html)

    def test_log_has_heading(self):
        html = webui.render_log(make_svc(), T)
        self.assertIn("<h1>%s</h1>" % T("log.title"), html)

    def test_log_no_repeated_run_status(self):
        html = webui.render_log(make_svc(last_run=NOW), T)
        self.assertNotIn("class='mut status'", html)


if __name__ == "__main__":
    unittest.main()
