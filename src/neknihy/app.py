from neknihy import katalog
from neknihy.api import API, catalogueIds, isAudiobook, webId
from neknihy.katalog import KatalogTSK
from neknihy.settings import Settings

import os.path
import json
import logging
import re
import subprocess
from datetime import datetime, timezone
from shutil import copy, move, rmtree

log = logging.getLogger("neknihy")

# Audiobook files: kept in workdir, but never sent to the e-reader / ingest
# folder (readers and Calibre cannot use them).
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".opus", ".flac", ".wav",
                    ".zip")


def isAudioFile(filename):
    return os.path.splitext(filename or "")[1].lower() in AUDIO_EXTENSIONS


def safeName(text, fallback="Audiokniha"):
    """A readable folder name: 'Agatha Christie' stays, only characters that
    file systems refuse are dropped."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", text or "")
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:120].rstrip(" .") or fallback


def catalogueName(name):
    """'Christie, Agatha' -> 'Agatha Christie'; dates ('…, 1974-') dropped."""
    parts = [p.strip() for p in name.split(",")]
    parts = [p for p in parts if p and not re.search(r"\d", p)]
    if len(parts) >= 2:
        return parts[1] + " " + parts[0]
    return parts[0] if parts else name.strip()


class App():
    def __init__(self, config=None, debug=False):
        self.api = API(debug)
        self.settings = Settings(config)
        self.settings.load()
        self.books = []
        self.katalog = None
        self.loadBooks()

    def updateSettings(self, email, password, workdir, readerdir, convert, convertor,
                       **extra):
        self.settings.update(email, password, workdir, readerdir, convert, convertor,
                             **extra)
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
        self.findWebIds()
        self.saveBooks()
        self.updateStatus()

    def findWebIds(self):
        """Look up loans in the library catalogue.

        The catalogue names only the real author(s), while Palmknihy lists
        translators and other contributors as authors too. Audiobook cover
        URLs also do not carry the palmknihy.cz ID (see api.webId), but the
        catalogue links each book to palmknihy.cz/kniha/<id>. Matched by title,
        and by author when several titles match. A loan that was looked up
        is not searched again; a failed search is retried next time.
        """
        for book in self.books:
            info = book.get("neknihy")
            if info is None or "authors" in info or not book.get("title"):
                continue
            audio = isAudiobook(book)
            try:
                res = katalog.search(book["title"], library=self.settings.tsk_library or "vkmr",
                                     kind="audiobook" if audio else "ebook", timeout=15)
            except Exception as e:
                log.info("%s not found in the catalogue: %s", book["title"], e)
                continue
            match = self._matchCatalogue(book, res.get("results", []))
            if audio and "web_id" not in info and webId(book) is None:
                info["web_id"] = match["palm_id"] if match else None
            info["authors"] = [catalogueName(a) for a in match.get("authors", [])]                 if match else []

    def _matchCatalogue(self, book, results):
        audio = isAudiobook(book)
        ids = catalogueIds(book)
        by_id = [r for r in results if r.get("palm_id") in ids]
        if by_id:
            return by_id[0]
        title = book["title"].strip().casefold()
        same = [r for r in results if r.get("palm_id") and
                bool(r.get("audiobook", audio)) == audio and
                (r.get("title") or "").strip().casefold() == title]
        if len(same) > 1:
            surnames = {(p.get("author") or {}).get("surname", "").casefold()
                        for p in book.get("persons", []) if p.get("type") == "author"}
            surnames.discard("")
            by_author = [r for r in same if any(
                n in "; ".join(r.get("authors", [])).casefold() for n in surnames)]
            same = by_author or same
        return same[0] if same else None

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

    def _expiredSafe(self, book):
        try:
            return self.bookExpired(book)
        except Exception:
            return False

    def updateStatus(self):
        changed = False
        # a deleted loan is forgotten once it expires
        kept = [b for b in self.books if not (b.get("neknihy", {}).get("status") == "deleted"
                                              and self._expiredSafe(b))]
        if len(kept) != len(self.books):
            self.books = kept
            changed = True
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

    def audioRoot(self):
        """Folder for audiobooks: the audiobook folder setting, else workdir."""
        return self.settings.audiodir or self.settings.workdir

    def audioLibrary(self):
        """True if audiobooks go to their own folder (e.g. an Audiobookshelf
        library), laid out as <Author>/<Title>/."""
        audiodir = self.settings.audiodir
        return bool(audiodir) and (
            os.path.normpath(os.path.abspath(audiodir)) !=
            os.path.normpath(os.path.abspath(self.settings.workdir or "")))

    def bookRoot(self, index):
        """Folder the book's file is in. Audiobooks remember where they were
        downloaded ("dir"), so changing the setting does not lose them."""
        book = self.books[index]
        if self.bookIsAudio(index):
            return book["neknihy"].get("dir") or self.settings.workdir
        return self.settings.workdir

    def bookFile(self, index):
        filename = self.books[index]["neknihy"]["filename"]
        if filename == "":
            return None
        return os.path.join(self.bookRoot(index), filename)

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
                # <Author>/ folder of an audiobook library, if now empty
                parent = os.path.dirname(path)
                root = self.bookRoot(index)
                if os.path.normpath(parent) != os.path.normpath(root):
                    try:
                        os.rmdir(parent)
                    except OSError:
                        pass
            else:
                os.remove(path)

    def bookDownloaded(self, index):
        book = self.book(index)
        if book is None:
            return False
        if "neknihy" not in book:
            return False
        if book["neknihy"]["status"] in ["ok", "expired"]:
            if not self.bookFileExists(index):
                return False
            if self.bookIsAudio(index) and book["neknihy"]["status"] == "ok":
                # audiobook folder setting changed: move it over (downloadBook)
                return self._sameDir(self.bookRoot(index), self.audioRoot())
            return True
        return False

    @staticmethod
    def _sameDir(a, b):
        return (os.path.normpath(os.path.abspath(a or "")) ==
                os.path.normpath(os.path.abspath(b or "")))

    def bookIsAudio(self, index):
        """True if the loan is an audiobook (or its file is an audio file)."""
        book = self.book(index)
        return bool(book) and (isAudiobook(book) or
                               isAudioFile(book.get("neknihy", {}).get("filename")))

    def bookAuthors(self, book):
        known = [a for a in (book.get("neknihy") or {}).get("authors") or [] if a]
        if known:
            # as the library catalogue names them
            return ", ".join(known)
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

    def audiobookFolder(self, book):
        """Folder for a new audiobook. In an audiobook library (Audiobookshelf)
        <Author>/<Title> – the names Audiobookshelf reads the book from;
        otherwise None (the default <title>-palmknihy in workdir)."""
        if not self.audioLibrary():
            return None
        authors = [a.strip() for a in self.bookAuthors(book).split(",") if a.strip()]
        title = safeName(book.get("title"))
        base = (safeName(" & ".join(authors)) + "/") if authors else ""
        root = self.audioRoot()
        folder = base + title
        n = 1
        # never mix chapters into a folder that is already there (e.g. the
        # same book bought elsewhere)
        while os.path.exists(os.path.join(root, folder)):
            n += 1
            folder = "%s%s (Palmknihy%s)" % (base, title, "" if n == 2 else " %d" % (n - 1))
        return folder

    def moveAudiobook(self, index):
        """Move an already downloaded audiobook to the current audiobook folder."""
        book = self.books[index]
        src = self.bookFile(index)
        root = self.audioRoot()
        folder = self.audiobookFolder(book) or book["neknihy"]["filename"]
        dst = os.path.join(root, folder)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        # via a hidden name, so a watching library sees only the whole book
        tmp = os.path.join(root, ".neknihy-move-%s" % book.get("palm_id"))
        if os.path.exists(tmp):
            rmtree(tmp)
        move(src, tmp)
        os.replace(tmp, dst)
        old_parent = os.path.dirname(src)
        if not self._sameDir(old_parent, self.bookRoot(index)):
            try:
                os.rmdir(old_parent)
            except OSError:
                pass
        book["neknihy"]["filename"] = folder
        book["neknihy"]["dir"] = root

    def downloadBook(self, index):
        book = self.books[index]
        if self.bookExpired(book):
            return
        if self.bookIsAudio(index):
            nk = book.get("neknihy", {})
            if nk.get("status") == "ok" and self.bookFileExists(index):
                return self.moveAudiobook(index)
            root = self.audioRoot()
            os.makedirs(root, exist_ok=True)
            self.api.downloadAudiobook(root, book, self.audiobookFolder(book),
                                       staging=self.audioLibrary())
            book["neknihy"]["dir"] = root
            return
        self.api.downloadBook(self.settings.workdir, book)

    def downloadBooks(self):
        if not self.settings.configured():
            return
        self.api.login(self.settings.email, self.settings.password)
        exc = None
        for i in range(len(self.books)):
            try:
                if (not self.bookDownloaded(i) and
                        self.books[i]["neknihy"]["status"] != "deleted"):
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

    def deleteBook(self, index):
        """Delete the loan's file. An expired loan is dropped from the list;
        an active one stays as "deleted" (hidden, not downloaded again), so
        refreshRents does not bring it back until it expires."""
        self.removeBookFile(index)
        if self.books[index]["neknihy"]["status"] == "expired":
            del self.books[index]
        else:
            self.books[index]["neknihy"].update(status="deleted", filename="")
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
