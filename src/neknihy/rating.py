"""Reader ratings of Palmknihy books (percent, as shown on palmknihy.cz).

The book page palmknihy.cz/kniha/<web id> carries schema.org JSON-LD with
``"aggregateRating": {"ratingValue": "85", "bestRating": 100, "ratingCount":
11}``. Books nobody has rated have no aggregateRating. The web ID is the one
the library catalogue links to (see katalog search results) and the one in
a loan's cover URL (see api.catalogueIds).

Pages are fetched in parallel and kept in a small JSON cache, so the web UI
does not hit palmknihy.cz on every page view.

    ratings = Ratings("/config/ratings.json")
    ratings.fetch([115312, 429088])
    ratings.get(115312)   # {"percent": 85, "count": 11}
    ratings.get(429088)   # {"percent": None, "count": 0}  (not rated yet)
    ratings.get(1)        # None  (not known yet)
"""

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from neknihy.katalog import USER_AGENT

log = logging.getLogger("neknihy")

BOOK_URL = "https://www.palmknihy.cz/kniha/%d"
TTL = 3 * 24 * 3600     # s a rating is kept
ERROR_TTL = 3600        # s before a failed fetch is tried again
WORKERS = 8
MAX_IDS = 100           # per fetch() call

_AGG_RE = re.compile(r'"aggregateRating"\s*:\s*\{(.*?)\}', re.S)
_VALUE_RE = re.compile(r'"ratingValue"\s*:\s*"?(\d+(?:[.,]\d+)?)')
_COUNT_RE = re.compile(r'"ratingCount"\s*:\s*"?(\d+)')
_BEST_RE = re.compile(r'"bestRating"\s*:\s*"?(\d+(?:[.,]\d+)?)')


def parse_rating(html):
    """{"percent": int or None, "count": int} from a palmknihy.cz book page."""
    m = _AGG_RE.search(html or "")
    if not m:
        return {"percent": None, "count": 0}
    agg = m.group(1)
    value = _VALUE_RE.search(agg)
    if not value:
        return {"percent": None, "count": 0}
    v = float(value.group(1).replace(",", "."))
    best = _BEST_RE.search(agg)
    b = float(best.group(1).replace(",", ".")) if best else 100.0
    if b and b != 100:
        v = v * 100 / b  # e.g. a 0-5 scale
    count = _COUNT_RE.search(agg)
    return {"percent": max(0, min(100, int(round(v)))),
            "count": int(count.group(1)) if count else 0}


class Ratings:
    def __init__(self, path=None, timeout=15):
        self.path = path
        self.timeout = timeout
        self.lock = threading.Lock()
        self.data = {}  # str(web id) -> {"percent", "count", "time"[, "error"]}
        self._load()

    def _load(self):
        if not self.path:
            return
        try:
            with open(self.path) as f:
                self.data = json.load(f)
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("Cannot read %s: %s", self.path, e)

    def _save(self):
        if not self.path:
            return
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.data, f)
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("Cannot write %s: %s", self.path, e)

    def _fresh(self, entry, now):
        ttl = ERROR_TTL if entry.get("error") else TTL
        return now - entry.get("time", 0) < ttl

    def get(self, web_id):
        """Cached {"percent", "count"}, or None when not known (yet)."""
        if web_id is None:
            return None
        entry = self.data.get(str(web_id))
        if not entry or entry.get("error") or not self._fresh(entry, time.time()):
            return None
        return {"percent": entry.get("percent"), "count": entry.get("count", 0)}

    def missing(self, ids):
        """IDs (in the given order, no duplicates) without a fresh entry."""
        now = time.time()
        out = []
        for i in ids:
            if i is None or i in out:
                continue
            entry = self.data.get(str(i))
            if not entry or not self._fresh(entry, now):
                out.append(i)
        return out

    def _fetch_one(self, web_id):
        try:
            r = requests.get(BOOK_URL % web_id, timeout=self.timeout,
                             headers={"User-Agent": USER_AGENT})
            if r.status_code == 404:
                return web_id, {"percent": None, "count": 0}
            r.raise_for_status()
            return web_id, parse_rating(r.text)
        except Exception as e:
            log.info("Rating of palmknihy.cz/kniha/%s not loaded: %s", web_id, e)
            return web_id, {"error": str(e)[:200]}

    def fetch(self, ids):
        """Load the ratings that are not cached; return {id: rating or None}."""
        ids = [int(i) for i in ids if i is not None][:MAX_IDS]
        todo = self.missing(ids)
        if todo:
            with ThreadPoolExecutor(max_workers=min(WORKERS, len(todo))) as pool:
                results = list(pool.map(self._fetch_one, todo))
            now = time.time()
            with self.lock:
                for web_id, entry in results:
                    entry["time"] = now
                    self.data[str(web_id)] = entry
                self._prune(now)
                self._save()
        return {i: self.get(i) for i in ids}

    def _prune(self, now):
        for k in [k for k, e in self.data.items() if now - e.get("time", 0) > TTL]:
            del self.data[k]
