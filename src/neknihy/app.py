from neknihy.api import API, isAudiobook
from neknihy.katalog import KatalogTSK
from neknihy.settings import Settings

import os.path
import json
import re
import subprocess
from datetime import datetime, timezone
from shutil import copy, rmtree

# Audiobook files: kept in workdir, but never sent to the e-reader / ingest
# folder (readers and Calibre cannot use them).
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".opus", ".flac", ".wav",
                    ".zip")


def isAudioFile(filename):
    return os.path.splitext(filename or "")[1].lower() in AUDIO_EXTENSIONS


class App():
    def __init__(self, config=None, debug=False):
        self.api = API(debug)
        self.settings = Settings(config)
        self.settings.load()
        self.books = []
        self.katalog = None
        self.loadBooks()

    def updateSettings(self, email, password, workdir, readerdir, convert, convertor,
                       **katalog):
        self.settings.update(email, password, workdir, readerdir, convert, convertor,
                             **katalog)
        self.api.logout()
        self.katalog = None
        self.loadBooks()

    def borrowFromKatalog(self, work_id):
        """Borrow an e-book or audiobook in the library catalogue (katalog.tsk.sk)."""
        s = self.settings
        if not s.katalog_configured():
            raise RuntimeError("Library catalogue login is not set")
        if self.katalog is None:
            self.katalog = KatalogTSK(s.tsk_username, s.tsk_password, s.tsk_library)
        try:
            return self.katalog.borrow(work_id)
        except Exception:
            self.katalog = None  # start with a fresh session next time
            raise

    def saveBooks(self):
        if not self.settings.configured():
            return
        file = os.path.join(self.settings.workdir, '.data')
        with open(file, 'w') as f:
            json.dump(self.books, f, indent=2)

    def loadBooks(self):
        try:
            if not self.settings.configured():
                return
            file = os.path.join(self.settings.workdir, '.data')
            with open(file) as f:
                self.books = json.load(f)
            self.updateStatus()
        except Exception:
            self.books = []

    def refreshRents(self):
        if not self.settings.configured():
            return
        self.api.login(self.settings.email, self.settings.password)
        rents = self.api.getListOfRentedBooks()
        for rent in rents:
            if not self.bookExpired(rent):
                if self.bookIndexByPalmId(rent["palm_id"], rent.get("item_type")) is None:
                    rent["neknihy"] = {"status": "new", "filename": ""}
                    self.books.append(rent)
        self.saveBooks()
        self.updateStatus()

    def dateFromString(self, date):
        try:
            return datetime.fromisoformat(date)
        except Exception:
            pass
        try:
            y = None
            m = None
            d = None
            if re.match(r'^\d{4}-\d{2}-\d{2}', date):
                y = date[0:4]
                m = date[5:7]
                d = date[8:10]
            if re.match(r'^\d{6}', date):
                y = date[0:4]
                m = date[4:6]
                d = date[6:8]
            if y is not None:
                return datetime(int(y), int(m), int(d), tzinfo=timezone.utc)
        except Exception:
            pass
        return datetime.now(timezone.utc)

    def bookExpired(self, book):
        if "end_time" in book:
            time = self.dateFromString(book["end_time"])
            if time < datetime.now(timezone.utc):
                return True
        return False

    def updateStatus(self):
        changed = False
        for book in self.books:
            try:
                if self.bookExpired(book) and book["neknihy"]["status"] != "expired":
                    book["neknihy"]["status"] = "expired"
                    changed = True
            except Exception:
                pass
        if changed:
            self.saveBooks()

    def bookIndexByPalmId(self, id, item_type=None):
        """Index of the loan with this palm_id. E-books and audiobooks have
        separate IDs, so item_type tells them apart (missing = e-book)."""
        audio = item_type == "audiobook"
        for i in range(len(self.books)):
            if self.books[i]["palm_id"] == id and isAudiobook(self.books[i]) == audio:
                return i
        return None

    def book(self, index):
        if index >= len(self.books):
            return None
        return self.books[index]

    def bookFile(self, index):
        filename = self.books[index]["neknihy"]["filename"]
        if filename == "":
            return None
        return os.path.join(self.settings.workdir, filename)

    def bookFileExists(self, index):
        path = self.bookFile(index)
        if path is None:
            return False
        return os.path.exists(path)

    def removeBookFile(self, index):
        if self.bookFileExists(index):
            path = self.bookFile(index)
            if os.path.isdir(path):  # audiobook: folder of chapters
                rmtree(path)
            else:
                os.remove(path)

    def bookDownloaded(self, index):
        book = self.book(index)
        if book is None:
            return False
        if "neknihy" not in book:
            return False
        if book["neknihy"]["status"] in ["ok", "expired"]:
            return self.bookFileExists(index)
        return False

    def bookIsAudio(self, index):
        """True if the loan is an audiobook (or its file is an audio file)."""
        book = self.book(index)
        return bool(book) and (isAudiobook(book) or
                               isAudioFile(book.get("neknihy", {}).get("filename")))

    def bookAuthors(self, book):
        authors = []
        result = ""
        try:
            for person in book["persons"]:
                if person["type"] == "author":
                    full = person["author"]["firstname"] + " " + person["author"]["surname"]
                    if full not in authors:
                        authors.append(full)
                        result += ", " + full
        except Exception:
            pass
        return result[2:]

    def downloadBook(self, index):
        if not self.bookExpired(self.books[index]):
            self.api.downloadBook(self.settings.workdir, self.books[index])

    def downloadBooks(self):
        if not self.settings.configured():
            return
        self.api.login(self.settings.email, self.settings.password)
        exc = None
        for i in range(len(self.books)):
            try:
                if not self.bookDownloaded(i):
                    self.downloadBook(i)
            except Exception as e:
                exc = e
        self.saveBooks()
        self.updateStatus()
        if exc is not None:
            raise exc

    def returnBooks(self):
        books = []
        for i in range(len(self.books)):
            if self.books[i]["neknihy"]["status"] == "expired":
                self.removeBookFile(i)
            else:
                books.append(self.books[i])
        self.books = books
        self.saveBooks()

    def bookByFilename(self, filename):
        filename = re.sub(".mobi$", ".epub", filename)
        for i in range(len(self.books)):
            if filename == self.books[i]["neknihy"]["filename"]:
                return i
        return None

    def syncReader(self):
        if self.settings.readerdir == "":
            return None
        if not os.path.exists(self.settings.readerdir):
            return None
        result = {"added": [], "removed": [], "total": 0}
        # add new books
        for i in range(len(self.books)):
            if self.books[i]["neknihy"]["status"] == "ok" and not self.bookIsAudio(i):
                src = self.bookFile(i)
                filename = self.books[i]["neknihy"]["filename"]
                ext = re.search("\\.[^.]+$", filename).group(0)
                if self.settings.convert == "1" and ext == ".epub":
                    filename = re.sub(".epub$", ".mobi", filename)
                dst = os.path.join(
                    self.settings.readerdir,
                    filename
                )
                if os.path.exists(src) and not os.path.exists(dst):
                    if self.settings.convert == "1" and ext == ".epub":
                        subprocess.run([self.settings.convertor, src, dst])
                    else:
                        copy(src, dst)
                    result["added"].append(self.books[i]["neknihy"]["filename"])
                result["total"] += 1
        # remove expired books
        for fn in os.listdir(self.settings.readerdir):
            if re.search("-palmknihy.[a-z0-9]+$", fn.lower()):
                if self.bookByFilename(fn) is None:
                    os.remove(os.path.join(self.settings.readerdir, fn))
                    result["removed"].append(fn)
        return result
