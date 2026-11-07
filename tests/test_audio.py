import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from neknihy.api import API, slugify  # noqa: E402
from neknihy.app import App, isAudioFile  # noqa: E402
import neknihy_cli  # noqa: E402


def response(content_type="", disposition=None):
    r = mock.Mock()
    r.headers = {"Content-Type": content_type}
    if disposition is not None:
        r.headers["Content-Disposition"] = disposition
    return r


class FileExtension(unittest.TestCase):
    def setUp(self):
        self.api = API()

    def test_ebooks(self):
        self.assertEqual(self.api.fileExtension(response("application/epub+zip")), ".epub")
        self.assertEqual(self.api.fileExtension(response("application/pdf")), ".pdf")

    def test_audio_from_file_name(self):
        r = response("application/octet-stream", 'attachment; filename="Kniha.M4B"')
        self.assertEqual(self.api.fileExtension(r), ".m4b")
        r = response("application/octet-stream", "attachment; filename=Kniha.zip")
        self.assertEqual(self.api.fileExtension(r), ".zip")

    def test_audio_from_content_type(self):
        self.assertEqual(self.api.fileExtension(response("audio/mpeg")), ".mp3")
        self.assertEqual(self.api.fileExtension(response("application/zip")), ".zip")

    def test_no_headers(self):
        self.assertEqual(self.api.fileExtension(response()), ".unknown")


class ReaderSync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = os.path.join(self.tmp.name, "work")
        self.reader = os.path.join(self.tmp.name, "reader")
        os.mkdir(self.work)
        os.mkdir(self.reader)
        self.app = App(config=os.path.join(self.tmp.name, "neknihy.ini"))
        s = self.app.settings
        s.workdir, s.readerdir, s.convert = self.work, self.reader, "0"
        self.app.books = []
        for pid, name in ((1, "kniha-palmknihy.epub"), (2, "audio-palmknihy.zip"),
                          (3, "audio2-palmknihy.mp3")):
            open(os.path.join(self.work, name), "w").close()
            self.app.books.append({"palm_id": pid,
                                   "neknihy": {"status": "ok", "filename": name}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_is_audio(self):
        self.assertTrue(isAudioFile("x.MP3"))
        self.assertTrue(isAudioFile("x.m4b"))
        self.assertFalse(isAudioFile("x.epub"))
        self.assertFalse(isAudioFile(""))
        self.assertFalse(isAudioFile(None))

    def test_reader_gets_only_ebooks(self):
        result = self.app.syncReader()
        self.assertEqual(result["added"], ["kniha-palmknihy.epub"])
        self.assertEqual(os.listdir(self.reader), ["kniha-palmknihy.epub"])
        # audiobooks stay in the work folder
        self.assertTrue(os.path.exists(os.path.join(self.work, "audio-palmknihy.zip")))

    def test_ingest_skips_audiobooks(self):
        with mock.patch.object(self.app.settings, "configured", return_value=False):
            added = neknihy_cli.sync_ingest(self.app)
        self.assertEqual(added, ["kniha-palmknihy.epub"])
        self.assertEqual(os.listdir(self.reader), ["kniha-palmknihy.epub"])


def jresp(data, status=200):
    r = mock.Mock()
    r.status_code = status
    r.text = json.dumps(data)
    r.headers = {"Content-Type": "application/json"}
    r.raise_for_status = mock.Mock()
    return r


AUDIO_RENT = {"palm_id": 75282, "variant_palm_id": 1, "item_type": "audiobook",
              "title": "Třináct záhad", "end_time": "2999-01-01T00:00:00Z"}
EBOOK_RENT = {"palm_id": 75282, "variant_palm_id": 1, "item_type": "ebook",
              "title": "Kniha", "end_time": "2999-01-01T00:00:00Z"}


class PalmknihyAudiobooks(unittest.TestCase):
    def setUp(self):
        self.api = API()
        self.api._login = {"token": "T"}
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_slugify(self):
        self.assertEqual(slugify("Třináct záhad"), "trinact-zahad")
        self.assertEqual(slugify("01_Uterni klub"), "01-uterni-klub")
        self.assertEqual(slugify(""), "audiokniha")

    @mock.patch("neknihy.api.requests.get")
    def test_rents_include_audiobooks(self, get):
        get.side_effect = [jresp({"results": [dict(EBOOK_RENT)]}),
                           jresp({"results": [dict(AUDIO_RENT)]})]
        rents = self.api.refreshListOfRentedBooks()
        self.assertEqual([r["item_type"] for r in rents], ["ebook", "audiobook"])
        self.assertIn("item_type=audiobook", get.call_args_list[1][0][0])

    @mock.patch("neknihy.api.requests.get")
    def test_rents_survive_audiobook_error(self, get):
        get.side_effect = [jresp({"results": [dict(EBOOK_RENT)]}), Exception("boom")]
        self.assertEqual(len(self.api.refreshListOfRentedBooks()), 1)

    def _chapter_get(self, processing=False):
        chapters = [{"id": 11, "name": "02_Druha", "position": 2},
                    {"id": 10, "name": "01_Prvni", "position": 1}]

        def get(url, headers=None, stream=False):
            if "user-chapters" in url:
                return jresp(chapters)
            if "/v3/audiobooks/file/75282/1/" in url:
                if processing:
                    return jresp({"status": False, "detail": "File is under processing"}, 501)
                cid = url.rstrip("/").rsplit("/", 1)[1]
                return jresp({"status": True,
                              "file": "https://cdn.example/%s.mp3?token=x" % cid})
            r = mock.Mock(status_code=200)
            r.iter_content.return_value = [b"ID3", url.encode()]
            return r
        return get

    def test_download_audiobook(self):
        book = dict(AUDIO_RENT)
        with mock.patch("neknihy.api.requests.get", side_effect=self._chapter_get()):
            self.api.downloadBook(self.tmp.name, book)
        self.assertEqual(book["neknihy"], {"status": "ok", "filename": "trinact-zahad-palmknihy"})
        folder = os.path.join(self.tmp.name, "trinact-zahad-palmknihy")
        self.assertEqual(sorted(os.listdir(folder)), ["01-prvni.mp3", "02-druha.mp3"])
        with open(os.path.join(folder, "01-prvni.mp3"), "rb") as f:
            self.assertIn(b"/10.mp3", f.read())

    def test_download_audiobook_resumes(self):
        folder = os.path.join(self.tmp.name, "trinact-zahad-palmknihy")
        os.makedirs(folder)
        with open(os.path.join(folder, "01-prvni.mp3"), "wb") as f:
            f.write(b"done")
        book = dict(AUDIO_RENT)
        get = mock.Mock(side_effect=self._chapter_get())
        with mock.patch("neknihy.api.requests.get", get):
            self.api.downloadBook(self.tmp.name, book)
        cdn = [c for c in get.call_args_list if "cdn.example" in c[0][0]]
        self.assertEqual([c[0][0] for c in cdn], ["https://cdn.example/11.mp3?token=x"])

    def test_download_audiobook_pending(self):
        book = dict(AUDIO_RENT)
        with mock.patch("neknihy.api.requests.get", side_effect=self._chapter_get(True)):
            self.api.downloadBook(self.tmp.name, book)
        self.assertEqual(book["neknihy"]["status"], "pending")


class AppAudiobooks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = App(config=os.path.join(self.tmp.name, "neknihy.ini"))
        self.app.settings.workdir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_palm_id_ebook_and_audiobook(self):
        self.app.books = [dict(EBOOK_RENT)]
        self.assertEqual(self.app.bookIndexByPalmId(75282), 0)
        self.assertEqual(self.app.bookIndexByPalmId(75282, "ebook"), 0)
        self.assertIsNone(self.app.bookIndexByPalmId(75282, "audiobook"))

    def test_return_removes_audiobook_folder(self):
        folder = os.path.join(self.tmp.name, "trinact-zahad-palmknihy")
        os.makedirs(folder)
        open(os.path.join(folder, "01.mp3"), "w").close()
        book = dict(AUDIO_RENT, neknihy={"status": "expired",
                                         "filename": "trinact-zahad-palmknihy"})
        self.app.books = [book]
        self.assertTrue(self.app.bookIsAudio(0))
        self.app.returnBooks()
        self.assertFalse(os.path.exists(folder))
        self.assertEqual(self.app.books, [])


if __name__ == "__main__":
    unittest.main()
