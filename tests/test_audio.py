import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from neknihy.api import API  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
