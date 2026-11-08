import requests
import json
import logging
import urllib
import os
import os.path
import re
import unicodedata
from http.client import HTTPConnection

log = logging.getLogger("neknihy")

AUDIOBOOK = "audiobook"


def isAudiobook(book):
    """True for a Palmknihy audiobook loan (item_type "audiobook")."""
    return (book or {}).get("item_type") == AUDIOBOOK


_COVER_ID_RE = re.compile(r"cover_(\d+)\.\w+$")


def catalogueIds(book):
    """IDs under which the library catalogue may link a loan.

    The catalogue links to palmknihy.cz/kniha/<id>. For older e-books that
    is the loan's palm_id, but newer ones (and audiobooks) have a different
    web ID, which only appears in the cover URL (.../cover_<id>.jpg).
    """
    book = book or {}
    ids = set()
    if not isAudiobook(book) and book.get("palm_id") is not None:
        ids.add(book["palm_id"])
    m = _COVER_ID_RE.search(book.get("cover") or "")
    if m:
        ids.add(int(m.group(1)))
    found = (book.get("neknihy") or {}).get("web_id")
    if found:
        ids.add(found)
    return ids


def webId(book):
    """The book's ID on palmknihy.cz (palmknihy.cz/kniha/<id>), or None.

    The cover URL carries it for e-books (older ones without it use their
    palm_id). Audiobook covers do not, so App.findWebIds() looks the ID up
    in the library catalogue and keeps it in book["neknihy"]["web_id"].
    """
    book = book or {}
    found = (book.get("neknihy") or {}).get("web_id")
    if found:
        return found
    m = _COVER_ID_RE.search(book.get("cover") or "")
    if m:
        return int(m.group(1))
    if not isAudiobook(book) and book.get("palm_id") is not None:
        return book["palm_id"]
    return None


def slugify(text):
    """'Třináct záhad' -> 'trinact-zahad' (safe as a file/folder name)."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text[:80].rstrip("-") or "audiokniha"


class API():
    def __init__(self, debug=False):
        self._url = "https://prodapp.palmknihy.cz"
        self._login = None
        self._rents = None
        if debug:
            HTTPConnection.debuglevel = 1

    def responseDebug(self, response):
        if HTTPConnection.debuglevel:
            print("---response begin---\n%s\n---response end ---" % response.text)

    def login(self, email, password):
        if self._login:
            return
        data = {"email": email, "password": password}
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        response = requests.post(self._url + "/users/login/",
                                 data=json.dumps(data),
                                 headers=headers)
        self.responseDebug(response)
        if response.status_code >= 300:
            raise RuntimeError("Nepodařilo se přihlásit, zkontrolujte jméno a heslo\n(%s)" %
                               response.text)
        self._login = json.loads(response.text)

    def logout(self):
        self._login = None
        self._rents = None

    def forgetRents(self):
        """Drop the cached loans list (keep the login) so it is fetched again."""
        self._rents = None

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer " + self._login["token"]
        }

    def refreshListOfRentedBooks(self):
        headers = self._headers()
        response = requests.get(self._url + "/rents/?refresh", headers=headers)
        self.responseDebug(response)
        if response.status_code >= 300:
            raise RuntimeError("Nepodařilo se načíst seznam zapůjčených knih\n(%s)" %
                               response.text)
        self._rents = json.loads(response.text)
        # /rents/ lists e-books only; audiobooks have to be asked for
        try:
            response = requests.get(self._url + "/rents/?refresh&item_type=" + AUDIOBOOK,
                                    headers=headers)
            self.responseDebug(response)
            response.raise_for_status()
            known = {(b.get("item_type"), b.get("palm_id")) for b in self._rents["results"]}
            for book in json.loads(response.text).get("results", []):
                book.setdefault("item_type", AUDIOBOOK)
                if (book.get("item_type"), book.get("palm_id")) not in known:
                    self._rents["results"].append(book)
        except Exception as e:
            log.warning("Could not load rented audiobooks: %s", e)
        return self._rents["results"]

    def getListOfRentedBooks(self):
        if self._rents:
            return self._rents["results"]
        return self.refreshListOfRentedBooks()

    def getRentDownloadInfo(self, book):
        headers = self._headers()
        url = (self._url + "/v3/books/file/" + str(book["palm_id"]) + "/" +
               str(book["variant_palm_id"]) + "/")
        response = requests.get(url, headers=headers)
        if response.status_code >= 300:
            raise RuntimeError("Nepodařilo se získat odkaz ke stažení výpujčky\n(%s)" %
                               response.text)
        return json.loads(response.text)

    # Content types of e-books and audiobooks (MP3 / M4B, often zipped)
    CONTENT_TYPES = {
        "/epub": ".epub",
        "/pdf": ".pdf",
        "/zip": ".zip",
        "/x-zip": ".zip",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/x-m4b": ".m4b",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
    }

    def fileExtension(self, response):
        ftype = (response.headers.get("Content-Type") or "").lower()
        # e-books: keep the long-standing behaviour
        if "/epub" in ftype:
            return ".epub"
        if "/pdf" in ftype:
            return ".pdf"

        # the file name is the most precise (e.g. .m4b vs .m4a)
        fdisposition = response.headers.get("Content-Disposition") or ""
        match = re.search('filename="?[^";]+(\\.[^";.]+)"?', fdisposition)
        if match:
            return match.group(1).lower()

        for key, ext in self.CONTENT_TYPES.items():
            if key in ftype:
                return ext

        return ".unknown"

    def getAudiobookChapters(self, book):
        """[{"id", "name", "length", "position"}, ...] of a rented audiobook."""
        url = self._url + "/v3/audiobooks/user-chapters/" + str(book["palm_id"]) + "/"
        response = requests.get(url, headers=self._headers())
        self.responseDebug(response)
        if response.status_code >= 300:
            raise RuntimeError("Nepodařilo se načíst kapitoly audioknihy\n(%s)" %
                               response.text)
        return sorted(json.loads(response.text), key=lambda c: c.get("position") or 0)

    def getChapterDownloadInfo(self, book, chapter):
        """{"status": true, "file": <signed URL>} or {"status": false, ...}."""
        url = "%s/v3/audiobooks/file/%s/%s/%s/" % (
            self._url, book["palm_id"], book.get("variant_palm_id") or 1, chapter["id"])
        response = requests.get(url, headers=self._headers())
        self.responseDebug(response)
        try:
            info = json.loads(response.text)
        except ValueError:
            info = None
        if isinstance(info, dict) and "status" in info:
            # "File is under processing" comes as HTTP 501 with status false
            return info
        raise RuntimeError("Nepodařilo se získat odkaz ke stažení kapitoly\n(%s)" %
                           response.text)

    def _fetch(self, url, path):
        """Download url to path (via path.part so a broken download is redone)."""
        response = requests.get(url, stream=True)
        if response.status_code >= 300:
            raise RuntimeError("Nepodařilo se stáhnout %s\n(%s)" %
                               (os.path.basename(path), response.text[:200]))
        part = path + ".part"
        with open(part, 'wb') as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
        os.replace(part, path)

    def downloadAudiobook(self, workdir, book, folder=None, staging=False):
        """Download every chapter (MP3) into workdir/<folder>/
        (default folder: <title>-palmknihy).

        Chapters already on disk are skipped, so an interrupted download
        continues where it stopped. With staging the chapters go to a hidden
        workdir/.neknihy-<title>/ first and the finished folder is moved in
        place at the end, so a watching library (Audiobookshelf) never sees
        a half-downloaded book.
        """
        folder = folder or slugify(book.get("title")) + "-palmknihy"
        final = os.path.join(workdir, folder)
        if staging:
            path = os.path.join(workdir, ".neknihy-" + slugify(book.get("title")) +
                                "-" + str(book.get("palm_id")))
        else:
            path = final
        chapters = self.getAudiobookChapters(book)
        if not chapters:
            book["neknihy"] = {"status": "pending", "filename": ""}
            return
        os.makedirs(path, exist_ok=True)
        width = max(2, len(str(len(chapters))))
        for n, chapter in enumerate(chapters, 1):
            info = self.getChapterDownloadInfo(book, chapter)
            if not info.get("status"):
                book["neknihy"] = {"status": "pending", "filename": ""}
                return
            url = info["file"]
            ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower() or ".mp3"
            # numbered by position so players keep the order; chapter names
            # often start with their own number ("01_Uterni klub")
            name = re.sub(r"^\d+-", "", slugify(chapter.get("name") or "kapitola"))
            fullpath = os.path.join(path, "%0*d-%s%s" % (width, n, name or "kapitola", ext))
            if os.path.exists(fullpath):
                continue
            log.info("Downloading %s: chapter %d/%d", book.get("title"), n, len(chapters))
            self._fetch(url, fullpath)
        if path != final:
            os.makedirs(os.path.dirname(final), exist_ok=True)
            os.replace(path, final)
        book["neknihy"] = {"status": "ok", "filename": folder}

    def downloadBook(self, workdir, book):
        if isAudiobook(book):
            return self.downloadAudiobook(workdir, book)
        downloadInfo = self.getRentDownloadInfo(book)
        if not downloadInfo["status"]:
            book["neknihy"] = {"status": "pending", "filename": ""}
            return
        info = downloadInfo["file"]
        params = urllib.parse.parse_qs(info.split('?', 1)[1])
        basename = params["filename"][0]
        if "url" in params and params["url"][0].lower().startswith("https://cdn.palmknihy.cz"):
            # old way
            url = params["url"][0]
        else:
            url = info
        fullpath = os.path.join(workdir, basename + ".part")
        response = requests.get(url, stream=True)
        with open(fullpath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=10240):
                if chunk:
                    f.write(chunk)
        if response.status_code >= 300:
            raise RuntimeError("Nepodařilo se stáhnout knihu %s\n(%s)" %
                               (basename, response.text))
        ext = self.fileExtension(response)
        os.rename(fullpath, os.path.join(workdir, basename + ext))
        book["neknihy"] = {"status": "ok", "filename": basename + ext}
