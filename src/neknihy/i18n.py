"""Minimal translations for the web UI (no dependencies).

Strings live in ``locales/<code>.json``. A missing key falls back to
English and then to the key itself, so a partial translation never breaks
the page. To add a language, copy ``en.json`` to ``<code>.json``, translate
the values and add a ``_name`` entry with the language's own name.
"""

import json
import os

LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")
FALLBACK = "en"


def _load():
    langs = {}
    for name in sorted(os.listdir(LOCALE_DIR)):
        if name.endswith(".json"):
            with open(os.path.join(LOCALE_DIR, name), encoding="utf-8") as f:
                langs[name[:-5]] = json.load(f)
    return langs


LANGS = _load()


def available():
    """[(code, native name), ...] in a stable order."""
    return [(code, data.get("_name", code)) for code, data in LANGS.items()]


def normalize(code):
    """Map 'sk-SK', 'de_AT', 'CS' ... to a known language code or None."""
    if not code:
        return None
    code = code.strip().lower().replace("_", "-").split("-")[0]
    return code if code in LANGS else None


def from_accept_language(header):
    """Pick the best supported language from an Accept-Language header."""
    best, best_q = None, -1.0
    for i, part in enumerate((header or "").split(",")):
        pieces = part.strip().split(";")
        code = normalize(pieces[0])
        if not code:
            continue
        q = 1.0
        for p in pieces[1:]:
            p = p.strip()
            if p.startswith("q="):
                try:
                    q = float(p[2:])
                except ValueError:
                    q = 0.0
        if q <= 0:
            continue
        score = q - i * 1e-6  # earlier entries win ties
        if score > best_q:
            best, best_q = code, score
    return best


class Translator:
    def __init__(self, lang):
        self.lang = normalize(lang) or FALLBACK
        self._data = LANGS.get(self.lang, {})
        self._fallback = LANGS.get(FALLBACK, {})

    def __call__(self, key, **kwargs):
        text = self._data.get(key)
        if text is None:
            text = self._fallback.get(key, key)
        return text.format(**kwargs) if kwargs else text
