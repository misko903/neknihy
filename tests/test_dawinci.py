import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from neknihy import dawinci, katalog  # noqa: E402

URL = dawinci.base_url("mkb")

# trimmed from a real result page of opac.mestskakniznica.sk
RECORD = """<div id="divChck_%(n)d" class="col-12 border-top-default" ><div class="col-2">
<img id="fmRFdivBookCover_C2QM__dwImg" src="https://coverlinker.biblib.net/?cid=AB&amp;x=1" alt="ikona"
 class="img_obalka_sm" onclick="javascript:window.location.assign(&quot;?fn=*recview&uid=%(uid)d&pageId=resultform&full=0&quot;);" />
</div><div class="col-8"><div class="well-sm"><b><i>%(title)s</i></b> / Agatha Christie : Kalibr, 2025. - ISBN %(isbn)s.<br /><br />
<a title="x" href="?fn=resultform&amp;keyWord=E-knihy">E-knihy</a>, <a title="x" href="?fn=resultform&amp;keyWord=Krimi%%2C+detektivky">Krimi, detektivky</a>, <a title="x" href="?fn=resultform&amp;keyWord=E-knihy">E-knihy</a><br /><br />
<a title="x" href="?fn=resultform&amp;author=Agatha+Christie">Agatha Christie</a><br /><br /><b>Anotácia:</b><br />Text</div></div>
<div class="col-2"><a href="?fn=*recview&amp;uid=%(uid)d&amp;pageId=resultform&amp;full=0&amp;focusName=bsktchRZ1#holdlistfocus">K dispozícii je e-kniha</a>
<a  id="anEbookE2QQ" onclick="javascript:ae(&#39;anEbookE2QQ&#39;,&#39;imgRecordEbooksClickAjax&#39;);" >Požičať e-knihu</a></div></div>"""


def page(records, total=None):
    body = "".join(RECORD % dict(r, n=i + 1) for i, r in enumerate(records))
    total = len(records) if total is None else total
    return ("<html><form id='submitform'><input name=\"fn\" id=\"fn\" type=\"hidden\" "
            "value=\"resultform\" /><input name=\"fc\" id=\"fc\" type=\"hidden\" "
            "value=\"0000006E\" /></form><div class=\"pull-left\">Podmienky hľadania: x<br />"
            "Počet zobrazených / nájdených záznamov: <b>%d / %d</b></div>%s</html>"
            % (min(total, 1000), total, body))


PRASKLE = {"uid": 329523, "title": "Prasklé zrcadlo", "isbn": "9788024296401"}
PROCES = {"uid": 303673, "title": "Ďáblův proces", "isbn": "9990004167919"}
NOTHING = '<html><script>window.location.assign("?fn=searchform&nothing=true");</script></html>'


def resp(text):
    r = mock.Mock()
    r.text = text
    r.url = URL
    r.raise_for_status = mock.Mock()
    return r


class Parse(unittest.TestCase):
    def test_palm_from_isbn(self):
        self.assertEqual(dawinci.palm_from_isbn("9990004167919"), 416791)
        self.assertEqual(dawinci.palm_from_isbn("9990000086689"), 8668)
        self.assertIsNone(dawinci.palm_from_isbn("9788024296401"))
        # Supraphon audiobooks: not a Palmknihy ID
        self.assertIsNone(dawinci.palm_from_isbn("9999251644235"))
        self.assertIsNone(dawinci.palm_from_isbn(""))

    def test_results(self):
        res = dawinci.parse_results(page([PRASKLE, PROCES], total=24))
        self.assertEqual(res["total"], 24)
        a, b = res["results"]
        self.assertEqual(a["work_id"], 329523)
        self.assertEqual(a["title"], "Prasklé zrcadlo")
        self.assertEqual(a["authors"], ["Agatha Christie"])
        self.assertEqual(a["publish_info"], "Kalibr, 2025")
        self.assertEqual(a["isbn"], ["9788024296401"])
        self.assertEqual(a["keywords"], ["E-knihy", "Krimi, detektivky"])
        self.assertEqual(a["cover"], "")   # the catalogue's covers are a placeholder
        self.assertIsNone(a["palm_id"])
        self.assertTrue(a["ebook"])
        self.assertFalse(a["audiobook"])
        self.assertEqual(b["palm_id"], 416791)

    def test_audiobook_flag_comes_from_the_search(self):
        r, = dawinci.parse_results(page([PROCES]), audiobook=True)["results"]
        self.assertTrue(r["audiobook"])


class SearchOne(unittest.TestCase):
    def session(self, gets, posts=None):
        s = mock.MagicMock()
        s.__enter__.return_value = s
        s.get.side_effect = [resp(x) for x in gets]
        s.post.side_effect = [resp(x) for x in (posts or ["<html>form</html>"] * 3)]
        return s

    def test_first_page(self):
        s = self.session([page([PRASKLE])])
        with mock.patch.object(dawinci.requests, "Session", return_value=s):
            res = dawinci.search_one(URL, dawinci.F_TITLE, "zrcadlo", "ebook")
        self.assertEqual([r["work_id"] for r in res["results"]], [329523])
        data = s.post.call_args.kwargs["data"]
        self.assertEqual(data["condCombo1"], "13")       # Palmknihy e-books filter
        self.assertEqual(data["condCombo2"], "4")        # title
        self.assertEqual(data["srchEdit2"], "zrcadlo")
        self.assertEqual(data["fa"], "performSearch")

    def test_audiobooks_by_author(self):
        s = self.session([page([PROCES])])
        with mock.patch.object(dawinci.requests, "Session", return_value=s):
            res = dawinci.search_one(URL, dawinci.F_AUTHOR, "Hrabal", "audiobook")
        data = s.post.call_args.kwargs["data"]
        self.assertEqual((data["condCombo1"], data["condCombo2"]), ("14", "3"))
        self.assertTrue(res["results"][0]["audiobook"])

    def test_nothing_found(self):
        s = self.session([NOTHING])
        with mock.patch.object(dawinci.requests, "Session", return_value=s):
            res = dawinci.search_one(URL, dawinci.F_TITLE, "zzz", "ebook")
        self.assertEqual(res, {"total": 0, "results": []})

    def test_later_page_uses_the_navigator(self):
        s = self.session([page([PRASKLE], total=25), page([PROCES], total=25)])
        with mock.patch.object(dawinci.requests, "Session", return_value=s):
            res = dawinci.search_one(URL, dawinci.F_TITLE, "x", "ebook", page=3)
        nav = s.post.call_args_list[1].kwargs["data"]
        self.assertEqual(nav["fo"], "DWNavigator1")
        self.assertEqual(nav["aa"], "navigator:DWNavigator1_3")
        self.assertEqual(nav["fc"], "0000006E")
        self.assertEqual([r["work_id"] for r in res["results"]], [303673])

    def test_page_past_the_end(self):
        s = self.session([page([PRASKLE], total=5)])
        with mock.patch.object(dawinci.requests, "Session", return_value=s):
            res = dawinci.search_one(URL, dawinci.F_TITLE, "x", "ebook", page=2)
        self.assertEqual(res["results"], [])
        self.assertEqual(s.post.call_count, 1)

    def test_unexpected_page(self):
        s = self.session(["<html>maintenance</html>"])
        with mock.patch.object(dawinci.requests, "Session", return_value=s), \
                self.assertRaises(dawinci.DawinciError):
            dawinci.search_one(URL, dawinci.F_TITLE, "x", "ebook")


class Search(unittest.TestCase):
    def test_merges_title_and_author_and_fills_palm_ids(self):
        parts = {
            (dawinci.F_TITLE, "ebook"): dawinci.parse_results(page([PRASKLE, PROCES])),
            (dawinci.F_AUTHOR, "ebook"): dawinci.parse_results(page([PROCES])),
        }
        with mock.patch.object(dawinci, "search_one",
                               side_effect=lambda u, f, q, k, p, t: parts[(f, k)]), \
                mock.patch.object(dawinci, "_palm_id", return_value=420243) as palm:
            res = dawinci.search("christie", "mkb")
        self.assertEqual([r["work_id"] for r in res["results"]], [329523, 303673])
        self.assertEqual([r["palm_id"] for r in res["results"]], [420243, 416791])
        palm.assert_called_once_with(URL, 329523, 30)   # only the one without a 9990 ISBN
        self.assertEqual(res["results"][1]["cover"],
                         "https://core.palmknihy.cz/api/books/416791/cover/400")
        self.assertEqual(res["total"], 2)
        self.assertEqual(res["pages"], 1)
        self.assertEqual(res["results"][0]["detail_url"],
                         URL + "?fn=*recview&uid=329523&full=0")

    def test_all_searches_both_kinds(self):
        calls = []

        def one(u, f, q, k, p, t):
            calls.append((f, k))
            return {"total": 0, "results": []}
        with mock.patch.object(dawinci, "search_one", side_effect=one):
            res = dawinci.search("x", "mkb", kind="all")
        self.assertEqual(sorted(calls), sorted([
            (dawinci.F_TITLE, "ebook"), (dawinci.F_AUTHOR, "ebook"),
            (dawinci.F_TITLE, "audiobook"), (dawinci.F_AUTHOR, "audiobook")]))
        self.assertEqual(res["results"], [])

    def test_palm_id_cache(self):
        dawinci._palm_cache.clear()
        r = resp('<a href="https://www.palmknihy.cz/kniha/420243">x</a>')
        with mock.patch.object(dawinci.requests, "get", return_value=r) as get:
            self.assertEqual(dawinci._palm_id(URL, 329523, 5), 420243)
            self.assertEqual(dawinci._palm_id(URL, 329523, 5), 420243)
        get.assert_called_once()


class KatalogDispatch(unittest.TestCase):
    def test_search_goes_to_dawinci(self):
        with mock.patch.object(dawinci, "search", return_value={"results": []}) as d:
            katalog.search("x", library="mkb", kind="audiobook", page=2)
        d.assert_called_once_with("x", "mkb", kind="audiobook", page=2, timeout=30)

    def test_library_list_and_borrowing(self):
        self.assertIn("mkb", dict(katalog.LIBRARIES))
        self.assertFalse(katalog.can_borrow("mkb"))
        self.assertTrue(katalog.can_borrow("vkmr"))
        self.assertTrue(katalog.can_borrow(""))

    def test_detail_url(self):
        self.assertEqual(katalog.detail_url("vkmr", {"work_id": 5}),
                         "https://katalog.tsk.sk/library/vkmr/detail/5")
        self.assertEqual(katalog.detail_url("mkb", {"work_id": 5, "detail_url": "u"}), "u")


if __name__ == "__main__":
    unittest.main()
