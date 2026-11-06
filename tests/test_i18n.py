import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from neknihy import i18n  # noqa: E402

PLACEHOLDER = re.compile(r"\{(\w+)\}")


class LocaleFiles(unittest.TestCase):
    def test_all_languages_have_the_same_keys(self):
        ref = set(i18n.LANGS["en"])
        for code, data in i18n.LANGS.items():
            with self.subTest(lang=code):
                self.assertEqual(set(data), ref)

    def test_placeholders_match_english(self):
        for key, text in i18n.LANGS["en"].items():
            want = set(PLACEHOLDER.findall(text))
            for code, data in i18n.LANGS.items():
                with self.subTest(lang=code, key=key):
                    self.assertEqual(set(PLACEHOLDER.findall(data[key])), want)

    def test_expected_languages(self):
        self.assertTrue({"cs", "sk", "en", "de"} <= set(i18n.LANGS))


class Selection(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(i18n.normalize("sk-SK"), "sk")
        self.assertEqual(i18n.normalize("DE_at"), "de")
        self.assertIsNone(i18n.normalize("xx"))
        self.assertIsNone(i18n.normalize(""))

    def test_accept_language(self):
        f = i18n.from_accept_language
        self.assertEqual(f("sk-SK,sk;q=0.9,cs;q=0.8,en;q=0.7"), "sk")
        self.assertEqual(f("fr-FR,fr;q=0.9,de;q=0.8,en;q=0.5"), "de")
        self.assertEqual(f("en;q=0.3,cs;q=0.9"), "cs")
        self.assertEqual(f("de,en"), "de")
        self.assertIsNone(f("fr,it"))
        self.assertIsNone(f("cs;q=0"))
        self.assertIsNone(f(None))

    def test_fallback_to_english_then_key(self):
        t = i18n.Translator("sk")
        self.assertEqual(t("btn.run"), "Spustiť všetko")
        t._data = {}
        self.assertEqual(t("btn.run"), "Run all")
        self.assertEqual(t("no.such.key"), "no.such.key")
        self.assertEqual(i18n.Translator("xx").lang, "en")


if __name__ == "__main__":
    unittest.main()
