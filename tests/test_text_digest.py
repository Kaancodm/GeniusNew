"""The digest worker: statistics a reader can recompute by hand.

Every expectation below is hand-countable from its sample, which is the
point of an extractive tool: if the scoring ever changes silently, these
tests are where the argument happens.
"""

import unittest

from geniusnew.config import BUILTIN_WORKERS
from geniusnew.workers import (TextDigest, _core_sentences, _figures, _flesch,
                               _keywords, _language, _sentences, _words,
                               render_digest)

GERMAN = (
    "Die Sicherheit der Audit Kette hängt am Anker. "
    "Der Anker signiert jeden Kopf der Kette. "
    "Ein Wetterbericht folgt weiter unten ganz ohne Zusammenhang zum Thema. "
    "Die Kette wächst mit jedem Job und der Anker hält die Sicherheit fest. "
    "Ohne den Anker wäre die Sicherheit der Kette nur eine Behauptung. "
    "Aktuell sind 62.137 Records die Grenze und 85 % der Jobs sind klein."
)
ENGLISH = (
    "The gateway checks every handoff and the anchor signs the head. "
    "Workers run isolated and the results are verified before they count."
)


class TextDigestTest(unittest.TestCase):
    def run_digest(self, text):
        return TextDigest().run({"text": text})["text"]

    def test_counts_are_hand_countable(self):
        digest = self.run_digest("Der Hund läuft schnell. Die Katze schläft.")
        self.assertIn("7 Wörter", digest)
        self.assertIn("2 Sätze", digest)
        self.assertEqual(_words("Eins zwei-drei vier's"), ["Eins", "zwei-drei", "vier's"])
        self.assertEqual(len(_sentences("Erster Satz! Zweiter? Dritter.")), 3)

    def test_keywords_rank_by_frequency_then_alphabet(self):
        # Hand-counted in GERMAN: anker 4, kette 4, sicherheit 3.
        lowered = [word.lower() for word in _words(GERMAN)]
        ranked = _keywords(lowered)
        self.assertEqual(ranked[:3],
                         [("anker", 4), ("kette", 4), ("sicherheit", 3)])

    def test_core_sentences_quote_the_text_and_skip_the_filler(self):
        core = _core_sentences(_sentences(GERMAN),
                               _keywords([w.lower() for w in _words(GERMAN)]))
        self.assertTrue(core)
        self.assertTrue(all(sentence in GERMAN for sentence in core))
        self.assertFalse(any("Wetterbericht" in sentence for sentence in core))

    def test_figures_keep_units_and_appearance_order(self):
        self.assertEqual(_figures(GERMAN), ["62.137 Records", "85 %"])
        self.assertEqual(_figures("rund 4,1 Stunden und 16 MiB"),
                         ["4,1 Stunden", "16 MiB"])

    def test_language_switches_labels_and_formula(self):
        self.assertEqual(_language([w.lower() for w in _words(GERMAN)]), "de")
        self.assertEqual(_language([w.lower() for w in _words(ENGLISH)]), "en")
        english = self.run_digest(ENGLISH)
        self.assertIn("Language: en", english)
        self.assertIn("Reading time", english)
        # "Der Hund läuft." — 3 words, 3 syllables, Amstad clamps to 100.
        self.assertEqual(_flesch(3, 1, 3, "de"), 100)
        score = _flesch(20, 2, 30, "en")
        self.assertTrue(0 <= score <= 100)

    def test_empty_and_wordless_texts_fail_as_worker_failures(self):
        for text in ("", "   \n  ", "123 456 !!!"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    render_digest(text)
        with self.assertRaises(KeyError):
            TextDigest().run({})

    def test_output_shape_bounds_and_determinism(self):
        monster = ("Wachstum " * 400) + ("Sicherheit Anker Kette. " * 200)
        first = TextDigest().run({"text": monster})
        second = TextDigest().run({"text": monster})
        self.assertEqual(first, second)
        self.assertEqual(set(first), {"text"})
        self.assertLessEqual(len(first["text"]), 6000)

    def test_long_stopword_only_fallback_keeps_statistics(self):
        # No ranked keywords: the fallback must not consume the 6,000-char budget.
        text = ("der " * 1700).strip() + "."
        digest = render_digest(text)
        core = digest.split("## Kern in Sätzen\n\n", 1)[1].split("\n\n", 1)[0]
        self.assertLessEqual(len(core), 320)
        self.assertLessEqual(len(digest), 6000)
        self.assertIn("## Kennzahlen", digest)
        self.assertIn("- Wörter: 1.700\n", digest)
        self.assertIn("- Sätze: 1\n", digest)
        self.assertNotIn("[gekürzt]", digest)
        self.assertTrue(core.endswith(" …"))
        self.assertTrue(text.startswith(core[:-2]))

    def test_stopword_only_fallback_at_or_below_limit_is_unchanged(self):
        for text in ("Der und die.", ("der " * 80).strip() + "."):
            with self.subTest(length=len(text)):
                digest = render_digest(text)
                core = digest.split("## Kern in Sätzen\n\n", 1)[1].split("\n\n", 1)[0]
                self.assertEqual(core, text)
                self.assertIn("## Kennzahlen", digest)
                self.assertNotIn("[gekürzt]", digest)

    def test_the_tool_is_registered_for_the_server(self):
        self.assertIs(BUILTIN_WORKERS["digest"], TextDigest)
        self.assertEqual(TextDigest.tool, "digest")


if __name__ == "__main__":
    unittest.main()
