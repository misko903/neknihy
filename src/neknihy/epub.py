"""Removing the social DRM page that Palmknihy adds to downloaded EPUBs.

The page (Text/social-drm.xhtml) carries the buyer's name and transaction,
it sits right after the cover in the spine. It is dropped from the archive,
the manifest, the spine, the guide and the table of contents; the rest of
the book stays byte for byte the same.
"""
import logging
import os
import posixpath
import re
import urllib.parse
import zipfile

log = logging.getLogger(__name__)

_NAME = re.compile(r"social[-_ ]?drm", re.I)
# the page's own markup: <p class="drm-name">, <p class="drm-transaction">
_MARKUP = re.compile(r"class=[\"']drm-(?:name|transaction|copy)\b")
_MAX_PAGE = 64 * 1024


def _attr(tag, name):
    m = re.search(r"\b%s\s*=\s*([\"'])(.*?)\1" % name, tag, re.S)
    return m.group(2) if m else None


def _resolve(base_dir, href):
    href = urllib.parse.unquote(href.split("#", 1)[0])
    return posixpath.normpath(posixpath.join(base_dir, href)) if href else ""


def _opf_path(z):
    container = z.read("META-INF/container.xml").decode("utf-8", "replace")
    m = re.search(r"<(?:\w+:)?rootfile\b[^>]*>", container)
    path = _attr(m.group(0), "full-path") if m else None
    if not path:
        raise ValueError("EPUB without a rootfile")
    return path


def _is_drm_page(z, path):
    if _NAME.search(posixpath.basename(path)):
        return True
    try:
        info = z.getinfo(path)
    except KeyError:
        return False
    if info.file_size > _MAX_PAGE:
        return False
    return bool(_MARKUP.search(z.read(path).decode("utf-8", "replace")))


def _drop_links(text, base_dir, targets):
    """Remove TOC entries (NCX navPoint, nav <li>) and guide references
    pointing to one of targets (paths inside the archive)."""
    def points_to(href):
        return href is not None and _resolve(base_dir, href) in targets

    # innermost NCX navPoints first, until nothing more goes away
    nav_point = re.compile(r"<(\w+:)?navPoint\b(?:(?!<(?:\w+:)?navPoint\b).)*?</\1?navPoint>",
                           re.S)
    li = re.compile(r"<(\w+:)?li\b(?:(?!<(?:\w+:)?li\b).)*?</\1?li>", re.S)
    for pattern, link in ((nav_point, r"<(?:\w+:)?content\b[^>]*>"),
                          (li, r"<(?:\w+:)?a\b[^>]*>")):
        while True:
            def repl(m):
                tags = re.findall(link, m.group(0))
                if tags and all(points_to(_attr(t, "src") or _attr(t, "href")) for t in tags):
                    return ""
                return m.group(0)
            new = pattern.sub(repl, text)
            if new == text:
                break
            text = new
    return re.sub(r"\s*<(?:\w+:)?reference\b[^>]*/>",
                  lambda m: "" if points_to(_attr(m.group(0), "href")) else m.group(0),
                  text)


def remove_social_drm(path):
    """Remove the social DRM page from the EPUB at path (in place).
    Returns True when a page was removed."""
    with zipfile.ZipFile(path) as z:
        opf_name = _opf_path(z)
        opf_dir = posixpath.dirname(opf_name)
        opf = z.read(opf_name).decode("utf-8")

        ids, targets = set(), set()
        for tag in re.findall(r"<(?:\w+:)?item\b[^>]*>", opf):
            href, item_id = _attr(tag, "href"), _attr(tag, "id")
            mtype = _attr(tag, "media-type") or ""
            if not href or "html" not in mtype:
                continue
            target = _resolve(opf_dir, href)
            if _is_drm_page(z, target):
                ids.add(item_id)
                targets.add(target)
        if not targets:
            return False

        def drop_tag(tag_name, attr, values):
            nonlocal opf
            opf = re.sub(r"[ \t]*<(?:\w+:)?%s\b[^>]*>[ \t]*\r?\n?" % tag_name,
                         lambda m: "" if _attr(m.group(0), attr) in values else m.group(0),
                         opf)
        drop_tag("item", "id", ids)
        drop_tag("itemref", "idref", ids)
        opf = _drop_links(opf, opf_dir, targets)

        # table of contents: NCX (EPUB 2) and nav document (EPUB 3)
        toc = {}
        for tag in re.findall(r"<(?:\w+:)?item\b[^>]*>", opf):
            mtype = _attr(tag, "media-type") or ""
            props = _attr(tag, "properties") or ""
            href = _attr(tag, "href")
            if href and ("ncx" in mtype or "nav" in props.split()):
                name = _resolve(opf_dir, href)
                if name in z.namelist():
                    text = z.read(name).decode("utf-8")
                    new = _drop_links(text, posixpath.dirname(name), targets)
                    if new != text:
                        toc[name] = new

        tmp = path + ".tmp"
        try:
            with zipfile.ZipFile(tmp, "w") as out:
                for info in z.infolist():
                    if info.filename in targets:
                        continue
                    if info.filename == opf_name:
                        data = opf.encode("utf-8")
                    elif info.filename in toc:
                        data = toc[info.filename].encode("utf-8")
                    else:
                        data = z.read(info)
                    # mimetype must stay first and uncompressed
                    out.writestr(info, data, compress_type=info.compress_type)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
    os.replace(tmp, path)
    log.info("Removed social DRM page from %s", os.path.basename(path))
    return True
