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
        self.assertTrue(url.endswith("/library/vkmr/process-login"))
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
