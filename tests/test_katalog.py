import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from neknihy import katalog  # noqa: E402
from neknihy.katalog import KatalogTSK, parse_work_id  # noqa: E402

HOME = "<html><head><meta name='_csrf' content='TOKEN-1'></head><body></body></html>"
FORM = "<form><input type='hidden' name='_csrf' value='TOKEN-2'></form>"


def resp(status=200, text="", location=None):
    r = mock.Mock()
    r.status_code = status
    r.text = text
    r.url = "https://katalog.tsk.sk/library/vkmr/"
    r.headers = {"Location": location} if location else {}
    r.raise_for_status = mock.Mock()
    return r


class ParseWorkId(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_work_id("1439847"), 1439847)
        self.assertEqual(parse_work_id(" 1439847 "), 1439847)
        self.assertEqual(parse_work_id(
            "https://katalog.tsk.sk/library/vkmr/jsapi/work?workId=1439847&order=X"), 1439847)
        self.assertEqual(parse_work_id(
            "https://katalog.tsk.sk/library/vkmr/eborrow-success/1439847/-1"), 1439847)
        self.assertIsNone(parse_work_id("hello"))
        self.assertIsNone(parse_work_id(""))
        self.assertIsNone(parse_work_id(None))


class Client(unittest.TestCase):
    def setUp(self):
        self.k = KatalogTSK("user", "pw", library="vkmr")
        self.k.session = mock.Mock()

    def test_csrf_from_meta_and_input(self):
        self.k.session.get.return_value = resp(text=HOME)
        self.assertEqual(self.k._get_csrf(), "TOKEN-1")
        self.k.session.get.return_value = resp(text=FORM)
        self.assertEqual(self.k._get_csrf(), "TOKEN-2")

    def test_missing_csrf(self):
        self.k.session.get.return_value = resp(text="<html></html>")
        with self.assertRaises(katalog.KatalogError):
            self.k._get_csrf()

    def test_login_ok(self):
        self.k.session.get.return_value = resp(text=HOME)
        self.k.session.post.return_value = resp(
            302, location="https://katalog.tsk.sk/library/vkmr")
        self.k.login()
        self.assertTrue(self.k.logged_in)
        url, = self.k.session.post.call_args.args
        data = self.k.session.post.call_args.kwargs["data"]
        # the real login handler is at the site root, not under /library/<lib>/
        self.assertEqual(url, "https://katalog.tsk.sk/process-login")
        self.assertEqual(data, {"wd": "vkmr", "target_url": "", "username": "user",
                                "password": "pw", "_csrf": "TOKEN-1"})

    def test_login_refused(self):
        self.k.session.get.return_value = resp(text=HOME)
        self.k.session.post.return_value = resp(
            302, location="https://katalog.tsk.sk/library/vkmr/login?error")
        with self.assertRaises(katalog.LoginError):
            self.k.login()
        self.assertFalse(self.k.logged_in)

    def test_borrow_ok(self):
        self.k.logged_in = True
        self.k.session.get.return_value = resp(text=HOME)
        ok = "https://katalog.tsk.sk/library/vkmr/eborrow-success/1439847/-1"
        self.k.session.post.return_value = resp(302, location=ok)
        self.assertEqual(self.k.borrow(1439847), ok)
        url, = self.k.session.post.call_args.args
        data = self.k.session.post.call_args.kwargs["data"]
        self.assertTrue(url.endswith("/library/vkmr/request-eborrow"))
        self.assertEqual(data, {"workId": "1439847", "providerId": "-1",
                                "agree": "on", "_csrf": "TOKEN-1"})

    def test_borrow_failed(self):
        self.k.logged_in = True
        self.k.session.get.return_value = resp(text=HOME)
        self.k.session.post.return_value = resp(
            302, location="https://katalog.tsk.sk/library/vkmr/work/1439847?error")
        with self.assertRaises(katalog.BorrowError):
            self.k.borrow(1439847)

    def test_borrow_relogin_once(self):
        self.k.logged_in = True
        self.k.session.get.return_value = resp(text=HOME)
        ok = "https://katalog.tsk.sk/library/vkmr/eborrow-success/1439847/-1"
        self.k.session.post.side_effect = [
            resp(302, location="https://katalog.tsk.sk/library/vkmr/login"),  # expired
            resp(302, location="https://katalog.tsk.sk/library/vkmr"),        # login
            resp(302, location=ok),                                           # retry
        ]
        self.assertEqual(self.k.borrow(1439847), ok)

    def test_borrow_login_not_accepted(self):
        self.k.session.get.return_value = resp(text=HOME)
        login = resp(302, location="https://katalog.tsk.sk/library/vkmr")
        again = resp(302, location="https://katalog.tsk.sk/library/vkmr/login")
        self.k.session.post.side_effect = [login, again, login, again]
        with self.assertRaises(katalog.LoginError):
            self.k.borrow(1439847)
        self.assertEqual(self.k.session.post.call_count, 4)

    def test_json_relogin_on_expired_session(self):
        self.k.logged_in = True
        data = resp(200)
        data.json = mock.Mock(return_value={"ok": True})
        self.k.session.get.side_effect = [
            resp(302, location="https://katalog.tsk.sk/library/vkmr/login"),  # expired
            resp(text=HOME),                                                   # login page
            data,                                                              # retry
        ]
        self.k.session.post.return_value = resp(
            302, location="https://katalog.tsk.sk/library/vkmr")
        self.assertEqual(self.k.user_data(), {"ok": True})


if __name__ == "__main__":
    unittest.main()


RESULT = """
<div class="pagination-info">Zobrazené záznamy 1 - 2 z celkom 2.</div>
<div class="works">
<div data-isbn="[9788024293097]" data-work-id="1481396" class="result harvested">
  <div class="cover">
    <a class="thumbnail" data-large-cover="https://core.palmknihy.cz/api/books/390655/cover/400"
       href="/library/vkmr/detail/1481396?search=x&amp;si=1"><img src="https://x/c.jpg"></a>
    <span class="work-type tt icon" title="" data-original-title="E-knihy - "></span>
  </div>
  <div class="showitems">
    <div class="Title"><span class="showitem"><span class="showitem-content"><a href="/d">
      <span class="showitem"><span class="showitem-content"><span class="highlight">Poirot</span>
      - Největší detektiv na světě</span></span></a></span></span><br></div>
    <div class="Authors"><span class="showitem"><span class="showitem-content">Aldridge, Mark</span>
      </span><br></div>
    <div class="Publish_info"><span class="showitem"><span class="showitem-label">Vydavateľské
      údaje: </span><span class="showitem-content">Euromedia 2023 </span></span><br></div>
    <div class="Keywords"><span class="showitem"><span class="showitem-label">Kľúčové slová:
      </span><span class="showitem-content">E-knihy</span></span><br><span class="showitem">
      <span class="showitem-content"><a href="/a">Povídky</a></span></span><span class="showitem">
      <span class="showitem-content">E-knihy</span></span></div>
  </div>
  <div class="work-availability btn-group" data-original-title="Dostupné v databázi: Palmknihy">
    <a class="btn" href=""><span>Zdroj:</span><span>Palmknihy</span></a>
    <ul><li><a href="https://www.palmknihy.cz/kniha/390655">kniha</a></li></ul>
  </div>
  <a class="btn" href="/library/vkmr/eborrow/1481396?search=x&amp;si=1">Požičať E-knihu</a>
  <form><input type="hidden" name="_csrf" value="T"></form>
</div>
<div data-work-id="42" class="result">
  <div class="showitems"><div class="Title"><span class="showitem-content">Tlačená kniha</span>
  </div><div class="Authors"><span class="showitem-content">A, B</span>
  <span class="showitem-content">C, D</span></div></div>
</div>
</div>
"""


AUDIO_RESULT = """
<div data-work-id="1452934" class="result harvested">
  <div class="showitems"><div class="Title"><span class="showitem-content">Postřelená
  (audio)kniha</span></div></div>
  <span class="work-type" data-original-title="audioknihy - "></span>
  <div class="work-availability btn-group" data-original-title="Dostupné v databázi: Palmknihy">
    <ul><li><a href="https://www.palmknihy.cz/kniha/401100">kniha</a></li></ul>
  </div>
  <a class="btn" href="/library/vkmr/eborrow/1452934?search=x&amp;si=1">Požičať</a>
</div>
"""


class Search(unittest.TestCase):
    def test_parse(self):
        data = katalog.parse_search(RESULT)
        self.assertEqual(data["total"], 2)
        first, second = data["results"]
        self.assertEqual(first["work_id"], 1481396)
        self.assertEqual(first["title"], "Poirot - Největší detektiv na světě")
        self.assertEqual(first["authors"], ["Aldridge, Mark"])
        self.assertEqual(first["publish_info"], "Euromedia 2023")
        self.assertEqual(first["isbn"], ["9788024293097"])
        self.assertEqual(first["work_type"], "E-knihy")
        self.assertEqual(first["keywords"], ["E-knihy", "Povídky"])
        self.assertTrue(first["ebook"])
        self.assertEqual(first["source"], "Palmknihy")
        self.assertEqual(first["palm_id"], 390655)
        self.assertEqual(first["cover"],
                         "https://core.palmknihy.cz/api/books/390655/cover/400")
        self.assertEqual(second["work_id"], 42)
        self.assertEqual(second["title"], "Tlačená kniha")
        self.assertEqual(second["authors"], ["A, B", "C, D"])
        self.assertFalse(second["ebook"])
        self.assertIsNone(second["palm_id"])

    def test_search_request(self):
        s = mock.Mock()
        s.get.return_value = resp(text=RESULT)
        data = katalog.search("poirot", library="vkmr", session=s)
        _, kwargs = s.get.call_args
        self.assertEqual(s.get.call_args[0][0], "https://katalog.tsk.sk/library/vkmr/search")
        self.assertEqual(kwargs["params"]["area"], 701)
        self.assertEqual(kwargs["params"]["q"], "poirot")
        self.assertEqual(kwargs["params"]["pageSize"], 100)
        self.assertEqual(data["pages"], 1)
        self.assertEqual(len(data["results"]), 2)

    def test_other_library_filters_ebooks(self):
        s = mock.Mock()
        s.get.return_value = resp(text=RESULT)
        data = katalog.search("poirot", library="other", session=s)
        self.assertEqual(s.get.call_args[1]["params"]["area"], -1)
        self.assertEqual([r["work_id"] for r in data["results"]], [1481396])

    def test_total_with_thousands_separator(self):
        html = RESULT.replace("z celkom 2.", "z celkom 1 800.")
        self.assertEqual(katalog.parse_search(html)["total"], 1800)
        html = RESULT.replace("z celkom 2.", "z celkom 12 345.")
        self.assertEqual(katalog.parse_search(html)["total"], 12345)

    def test_parse_audiobook(self):
        html = AUDIO_RESULT
        (r,) = katalog.parse_search(html)["results"]
        self.assertTrue(r["ebook"])
        self.assertTrue(r["audiobook"])
        self.assertEqual(r["palm_id"], 401100)
        first, second = katalog.parse_search(RESULT)["results"]
        self.assertFalse(first["audiobook"])
        self.assertFalse(second["audiobook"])

    def test_ebook_with_audiobook_keyword(self):
        # the catalogue tags many e-books "Audioknihy" too; the work type decides
        html = RESULT.replace(">Povídky<", ">Audioknihy<")
        first, _ = katalog.parse_search(html)["results"]
        self.assertIn("Audioknihy", first["keywords"])
        self.assertFalse(first["audiobook"])
        s = mock.Mock()
        s.get.return_value = resp(text=html)
        ids = [r["work_id"] for r in katalog.search("x", session=s, kind="ebook")["results"]]
        self.assertIn(1481396, ids)
        # without a work type the keywords still tell
        self.assertTrue(katalog.is_audiobook({"work_type": "", "keywords": ["Audioknihy"]}))

    def test_search_audiobooks_area(self):
        s = mock.Mock()
        s.get.return_value = resp(text=AUDIO_RESULT)
        data = katalog.search("x", library="vkmr", session=s, kind="audiobook")
        self.assertEqual(s.get.call_args[1]["params"]["area"], 702)
        self.assertEqual(data["kind"], "audiobook")
        s.get.reset_mock()
        s.get.return_value = resp(text=RESULT + AUDIO_RESULT)
        data = katalog.search("x", library="vkmr", session=s, kind="all")
        self.assertEqual([c[1]["params"]["area"] for c in s.get.call_args_list], [701, 702])
        # the same work from both areas is listed once
        self.assertEqual(len(data["results"]), 3)
        s.get.return_value = resp(text=RESULT + AUDIO_RESULT)
        ids = [r["work_id"] for r in katalog.search(
            "x", library="vkmr", session=s, kind="ebook")["results"]]
        self.assertNotIn(1452934, ids)  # audiobook in the e-book area is left out
        katalog.search("x", library="vkmr", session=s, kind="nonsense")
        self.assertEqual(s.get.call_args[1]["params"]["area"], 701)

    def test_other_library_filters_by_kind(self):
        s = mock.Mock()
        s.get.return_value = resp(text=RESULT + AUDIO_RESULT)
        ids = lambda kind: [r["work_id"] for r in katalog.search(  # noqa: E731
            "x", library="other", session=s, kind=kind)["results"]]
        self.assertEqual(ids("ebook"), [1481396])
        self.assertEqual(ids("audiobook"), [1452934])
        self.assertEqual(ids("all"), [1481396, 1452934])

    def _robot(self):
        r = resp(text="<html>check</html>")
        r.url = "https://katalog.tsk.sk/library/vkmr/i-am-not-a-robot?returnUrl=x"
        return r

    def test_bot_check_passed(self):
        s = mock.Mock()
        token = resp(text="")
        token.json = mock.Mock(return_value={"token": "abc-123"})
        s.get.side_effect = [self._robot(), token, resp(text=RESULT)]
        data = katalog.search("dahl", session=s)
        self.assertEqual(len(data["results"]), 2)
        self.assertEqual(s.get.call_args_list[1][0][0],
                         "https://katalog.tsk.sk/jsapi/pow/token")
        s.cookies.set.assert_called_once_with(
            "AuthToken", "abc-123", domain="katalog.tsk.sk", path="/")

    def test_bot_check_redirect_without_follow(self):
        r = resp(302, location="https://katalog.tsk.sk/library/vkmr/i-am-not-a-robot?x")
        self.assertTrue(katalog.is_bot_check(r))

    def test_bot_check_repeated(self):
        s = mock.Mock()
        token = resp(text="")
        token.json = mock.Mock(return_value={"token": "abc-123"})
        s.get.side_effect = [self._robot(), token, self._robot()]
        with self.assertRaises(katalog.BotCheckError):
            katalog.search("dahl", session=s)

    def test_bot_check_no_token(self):
        s = mock.Mock()
        token = resp(text="")
        token.json = mock.Mock(return_value={})
        s.get.side_effect = [self._robot(), token]
        with self.assertRaises(katalog.BotCheckError):
            katalog.search("dahl", session=s)
