import os
import sys
import tempfile
import unittest
import zipfile
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from neknihy.epub import remove_social_drm  # noqa: E402
from neknihy.settings import Settings  # noqa: E402
from neknihy.app import App  # noqa: E402

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

OPF = """<?xml version='1.0' encoding='UTF-8'?>
<opf:package version="2.0" xmlns:opf="http://www.idpf.org/2007/opf">
  <opf:manifest>
    <opf:item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml" />
    <opf:item id="nav" href="Text/nav.xhtml" media-type="application/xhtml+xml" properties="nav" />
    <opf:item id="cover" href="Text/cover.xhtml" media-type="application/xhtml+xml" />
    <opf:item id="social-drm" href="Text/social-drm.xhtml" media-type="application/xhtml+xml" />
    <opf:item id="ch1" href="Text/01%20Kapitola.xhtml" media-type="application/xhtml+xml" />
  </opf:manifest>
  <opf:spine toc="ncx">
    <opf:itemref idref="cover" />
    <opf:itemref idref="social-drm" />
    <opf:itemref idref="ch1" />
  </opf:spine>
  <opf:guide>
    <opf:reference type="cover" href="Text/cover.xhtml" title="cover" />
    <opf:reference type="copyright-page" href="Text/social-drm.xhtml" title="drm" />
  </opf:guide>
</opf:package>"""

NCX = """<ncx><navMap>
<navPoint id="n1"><navLabel><text>DRM</text></navLabel><content src="Text/social-drm.xhtml"/></navPoint>
<navPoint id="n2"><navLabel><text>Kapitola</text></navLabel><content src="Text/01%20Kapitola.xhtml"/></navPoint>
</navMap></ncx>"""

NAV = """<html><body><nav epub:type="toc"><ol>
<li><a href="social-drm.xhtml">DRM</a></li>
<li><a href="01%20Kapitola.xhtml">Kapitola</a></li>
</ol></nav></body></html>"""

DRM_PAGE = '<html><body><p class="drm-name"><em>Kupující:</em> Jan Novák</p></body></html>'
CHAPTER = '<html><body><p>Text knihy.</p></body></html>'


def make_epub(path, drm=True, drm_name="Text/social-drm.xhtml", opf=OPF):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", opf if drm else opf.replace("social-drm", "x"))
        z.writestr("OEBPS/toc.ncx", NCX)
        z.writestr("OEBPS/Text/nav.xhtml", NAV)
        z.writestr("OEBPS/Text/cover.xhtml", CHAPTER)
        z.writestr("OEBPS/Text/01 Kapitola.xhtml", CHAPTER)
        if drm:
            z.writestr("OEBPS/" + drm_name, DRM_PAGE)


class RemoveSocialDrmTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "kniha.epub")

    def tearDown(self):
        self.tmp.cleanup()

    def test_removes_page_everywhere(self):
        make_epub(self.path)
        self.assertTrue(remove_social_drm(self.path))
        with zipfile.ZipFile(self.path) as z:
            self.assertIsNone(z.testzip())
            names = z.namelist()
            self.assertEqual(names[0], "mimetype")
            self.assertEqual(z.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
            self.assertNotIn("OEBPS/Text/social-drm.xhtml", names)
            opf = z.read("OEBPS/content.opf").decode()
            ncx = z.read("OEBPS/toc.ncx").decode()
            nav = z.read("OEBPS/Text/nav.xhtml").decode()
            chapter = z.read("OEBPS/Text/01 Kapitola.xhtml").decode()
        self.assertNotIn("social-drm", opf)
        self.assertIn('idref="ch1"', opf)
        self.assertIn('href="Text/cover.xhtml"', opf)
        self.assertNotIn("social-drm", ncx)
        self.assertIn("Kapitola", ncx)
        self.assertNotIn("social-drm", nav)
        self.assertIn("Kapitola", nav)
        self.assertEqual(chapter, CHAPTER)

    def test_second_run_is_noop(self):
        make_epub(self.path)
        remove_social_drm(self.path)
        self.assertFalse(remove_social_drm(self.path))

    def test_without_drm_page_untouched(self):
        make_epub(self.path, drm=False)
        before = os.path.getmtime(self.path), os.path.getsize(self.path)
        self.assertFalse(remove_social_drm(self.path))
        self.assertEqual(before, (os.path.getmtime(self.path), os.path.getsize(self.path)))

    def test_detected_by_markup_under_other_name(self):
        make_epub(self.path, drm_name="Text/watermark.xhtml",
                  opf=OPF.replace("Text/social-drm.xhtml", "Text/watermark.xhtml"))
        self.assertTrue(remove_social_drm(self.path))
        with zipfile.ZipFile(self.path) as z:
            self.assertNotIn("OEBPS/Text/watermark.xhtml", z.namelist())


class SettingTest(unittest.TestCase):
    def test_on_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            s = Settings(os.path.join(d, "neknihy.conf"))
            s.load()
            self.assertEqual(s.remove_drm, "1")
            s.update(email="a@b.c", remove_drm="0")
            s2 = Settings(os.path.join(d, "neknihy.conf"))
            s2.load()
            self.assertEqual(s2.remove_drm, "0")

    def _app(self, workdir, remove_drm):
        app = App.__new__(App)
        app.settings = SimpleNamespace(workdir=workdir, remove_drm=remove_drm)
        return app

    def test_app_removes_after_download(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "kniha.epub")
            book = {"neknihy": {"status": "ok", "filename": "kniha.epub"}}
            make_epub(path)
            self._app(d, "0").removeDrmPage(book)
            with zipfile.ZipFile(path) as z:
                self.assertIn("OEBPS/Text/social-drm.xhtml", z.namelist())
            self._app(d, "1").removeDrmPage(book)
            with zipfile.ZipFile(path) as z:
                self.assertNotIn("OEBPS/Text/social-drm.xhtml", z.namelist())

    def test_app_keeps_broken_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "kniha.epub")
            with open(path, "wb") as f:
                f.write(b"not a zip")
            book = {"neknihy": {"status": "ok", "filename": "kniha.epub"}}
            with self.assertLogs("neknihy", "WARNING"):
                self._app(d, "1").removeDrmPage(book)
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"not a zip")


if __name__ == "__main__":
    unittest.main()
