import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from neknihy import rating  # noqa: E402
from neknihy.api import webId  # noqa: E402

RATED = """<script type="application/ld+json">
{
  "@context": "https://schema.org/",
  "@type": ["Product", "Book"],
  "name": "Z kolebky",
  "aggregateRating": {
    "@type": "AggregateRating",
    "ratingValue": "85",
    "bestRating": 100,
    "worstRating": 0,
    "ratingCount": 11
  }
  ,"review": [{"@type": "Review"}]
}
</script>"""

UNRATED = """<script type="application/ld+json">
{"@context": "https://schema.org/", "@type": ["Product", "Book"], "name": "X"}
</script>"""


def resp(text="", code=200):
    r = mock.Mock(status_code=code, text=text)
    r.raise_for_status = mock.Mock()
    return r


class ParseTest(unittest.TestCase):
    def test_rated(self):
        self.assertEqual(rating.parse_rating(RATED), {"percent": 85, "count": 11})

    def test_unrated(self):
        self.assertEqual(rating.parse_rating(UNRATED), {"percent": None, "count": 0})

    def test_other_scale(self):
        html = '"aggregateRating": {"ratingValue": "4.5", "bestRating": "5", "ratingCount": 2}'
        self.assertEqual(rating.parse_rating(html), {"percent": 90, "count": 2})


class RatingsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "ratings.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_fetch_caches_and_persists(self):
        pages = {rating.BOOK_URL % 1: resp(RATED), rating.BOOK_URL % 2: resp(UNRATED)}
        with mock.patch.object(rating.requests, "get",
                               side_effect=lambda url, **kw: pages[url]) as get:
            r = rating.Ratings(self.path)
            self.assertIsNone(r.get(1))
            out = r.fetch([1, 2, 1])
            self.assertEqual(out[1], {"percent": 85, "count": 11})
            self.assertEqual(out[2], {"percent": None, "count": 0})
            self.assertEqual(get.call_count, 2)
            r.fetch([1, 2])  # cached
            self.assertEqual(get.call_count, 2)
        self.assertEqual(rating.Ratings(self.path).get(1), {"percent": 85, "count": 11})

    def test_error_is_not_a_rating(self):
        with mock.patch.object(rating.requests, "get", side_effect=OSError("down")):
            r = rating.Ratings(self.path)
            self.assertEqual(r.fetch([5]), {5: None})
        self.assertEqual(r.missing([5]), [])  # not retried right away
        r.data["5"]["time"] = time.time() - rating.ERROR_TTL - 1
        self.assertEqual(r.missing([5]), [5])

    def test_expired(self):
        r = rating.Ratings(self.path)
        r.data["7"] = {"percent": 50, "count": 1, "time": time.time() - rating.TTL - 1}
        self.assertIsNone(r.get(7))
        self.assertEqual(r.missing([7, None]), [7])


class WebIdTest(unittest.TestCase):
    def test_cover_id(self):
        book = {"palm_id": 102228, "cover": "https://cdn/x/cover_429088.jpg"}
        self.assertEqual(webId(book), 429088)

    def test_palm_id_fallback(self):
        self.assertEqual(webId({"palm_id": 5}), 5)
        self.assertIsNone(webId({"palm_id": 5, "item_type": "audiobook"}))

    def test_found_web_id(self):
        book = {"palm_id": 75282, "item_type": "audiobook",
                "cover": "https://cdn.palmknihy.cz/x/a801v0f0010466_trinact-zahad.jpg",
                "neknihy": {"status": "ok", "web_id": 401774}}
        self.assertEqual(webId(book), 401774)


class FindWebIdsTest(unittest.TestCase):
    def setUp(self):
        from neknihy.app import App
        self.app = App.__new__(App)
        self.app.settings = mock.Mock(tsk_library="vkmr")
        self.book = {"palm_id": 75282, "item_type": "audiobook", "title": "Třináct záhad",
                     "persons": [{"type": "author",
                                  "author": {"firstname": "Agatha", "surname": "Christie"}}],
                     "neknihy": {"status": "ok"}}
        self.app.books = [self.book, {"palm_id": 1, "item_type": "ebook", "title": "X",
                                      "neknihy": {"status": "ok"}}]

    def results(self, *items):
        return {"results": [dict(title=t, palm_id=i, authors=a, audiobook=True)
                            for t, i, a in items]}

    def test_match_by_title_and_author(self):
        res = self.results(("Třináct záhad", 1, ["Novák, Jan"]),
                           ("třináct záhad ", 401774, ["Christie, Agatha"]),
                           ("Třináct záhad a jedna", 3, ["Christie, Agatha"]))
        with mock.patch("neknihy.app.katalog.search", return_value=res) as search:
            self.app.findWebIds()
            self.app.findWebIds()  # already known: not searched again
        search.assert_called_once()
        self.assertEqual(search.call_args.kwargs["kind"], "audiobook")
        self.assertEqual(webId(self.book), 401774)

    def test_authors_from_catalogue(self):
        self.book["persons"] += [{"type": "author",
                                  "author": {"firstname": "Jan", "surname": "Překladatel"}}]
        res = self.results(("Třináct záhad", 401774, ["Christie, Agatha"]))
        with mock.patch("neknihy.app.katalog.search", return_value=res):
            self.app.findWebIds()
        self.assertEqual(self.app.bookAuthors(self.book), "Agatha Christie")

    def test_known_web_id_still_gets_authors(self):
        self.book["neknihy"]["web_id"] = 5
        res = self.results(("Třináct záhad", 401774, ["Čermák, Jan, 1974-"]))
        with mock.patch("neknihy.app.katalog.search", return_value=res):
            self.app.findWebIds()
        self.assertEqual(self.book["neknihy"]["web_id"], 5)
        self.assertEqual(self.app.bookAuthors(self.book), "Jan Čermák")

    def test_not_found_is_remembered_error_is_retried(self):
        with mock.patch("neknihy.app.katalog.search", side_effect=OSError("down")):
            self.app.findWebIds()
        self.assertNotIn("web_id", self.book["neknihy"])
        with mock.patch("neknihy.app.katalog.search", return_value=self.results()):
            self.app.findWebIds()
        self.assertIsNone(self.book["neknihy"]["web_id"])
        self.assertIsNone(webId(self.book))


if __name__ == "__main__":
    unittest.main()
