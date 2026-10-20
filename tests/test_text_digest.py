"""The digest worker: statistics a reader can recompute by hand.

Every expectation below is hand-countable from its sample, which is the
point of an extractive tool: if the scoring ever changes silently, these
tests are where the argument happens.
"""

import unittest

from geniusnew.config import BUILTIN_WORKERS
from geniusnew.contracts import (Grant, HandoffSigner, Policy, canonical,
                                 issue, validate)
from geniusnew.results import WorkerAuthority, accept, produce
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

    def run_digest_on_wire(self, text):
        signer = HandoffSigner(integrity_key=b"digest-handoff-fixture-key-32-bytes")
        authority = WorkerAuthority(result_key=b"digest-result-fixture-key-32-bytes")
        grant = Grant("digest-test", "user", "worker", "basic",
                      ("digest",), "isolated", False)
        policy = Policy("policy-v1", "orchestrator", 60,
                        ("digest",), ("isolated",), (grant,))
        incoming = issue({"text": text}, subject="digest-test", job_id="digest-budget",
                         policy=policy, signer=signer, now=100)
        self.assertLessEqual(len(incoming), 16 * 1024)
        handoff = validate(incoming, subject="digest-test", job_id="digest-budget",
                           policy=policy, verifier=signer.verifier(), now=101)
        output = TextDigest().run(handoff.payload)
        outgoing = produce(output, handoff=handoff, status="SUCCEEDED",
                           reason_code="WORK_COMPLETED", authority=authority, now=110)
        self.assertLessEqual(len(outgoing), 16 * 1024)
        result = accept(outgoing, handoff=handoff,
                        verifier=authority.verifier(), now=111)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.output, output)
        return result.output["text"]

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

    def test_long_keyword_display_keeps_identity_and_statistics(self):
        word = "a" * 7000
        self.assertEqual(_keywords([word]), [(word, 1)])
        digest = render_digest(word + ".")
        self.assertTrue("## Kennzahlen" in digest, "statistics missing from digest")
        shown = digest.split("## Schlüsselbegriffe\n\n", 1)[1].split("\n\n", 1)[0]
        self.assertTrue(shown.endswith(" … (1)"))
        self.assertLessEqual(len(shown.removesuffix(" (1)")), 80)
        self.assertIn("- Wörter: 1\n", digest)
        self.assertIn("- Ø Zeichen pro Wort: 7000.0\n", digest)
        self.assertNotIn("[gekürzt]", digest)

    def test_keyword_display_at_or_below_limit_is_unchanged(self):
        for word in ("Anker", "a" * 80):
            with self.subTest(length=len(word)):
                digest = render_digest(f"{word} {word}.")
                shown = digest.split("## Schlüsselbegriffe\n\n", 1)[1].split("\n\n", 1)[0]
                self.assertEqual(shown, f"{word.lower()} (2)")

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

    def test_long_numeric_figure_keeps_statistics(self):
        text = "der " + "7" * 7000 + "."
        digest = render_digest(text)
        self.assertTrue("## Kennzahlen" in digest, "statistics missing from digest")
        shown = digest.split("## Zahlen im Text\n\n", 1)[1].split("\n\n", 1)[0]
        self.assertTrue(shown.startswith("- "))
        self.assertTrue(shown.endswith(" …"))
        self.assertLessEqual(len(shown.removeprefix("- ")), 320)
        self.assertIn("- Wörter: 1\n", digest)
        self.assertIn("- Sätze: 1\n", digest)
        self.assertNotIn("[gekürzt]", digest)

    def test_figures_at_or_below_limit_are_unchanged(self):
        for figure in ("16 MiB", "7" * 317 + " kg"):
            with self.subTest(length=len(figure)):
                self.assertEqual(_figures(figure), [figure])

    def test_figures_deduplicate_the_displayed_excerpt(self):
        prefix = "7" * 318
        shown = _figures(f"{prefix}111 und {prefix}222")
        self.assertEqual(len(shown), 1)
        self.assertTrue(shown[0].endswith(" …"))
        self.assertTrue(prefix.startswith(shown[0][:-2]))
        self.assertLessEqual(len(canonical(shown[0])) - 2, 320)

    def test_non_bmp_figures_fit_the_signed_result_wire(self):
        figures = [chr(0x1D7CE + index) * 130 for index in range(8)]
        digest = self.run_digest_on_wire("der " + "; ".join(figures) + ".")
        shown = digest.split("## Zahlen im Text\n\n", 1)[1].split("\n\n", 1)[0].splitlines()
        self.assertEqual(len(shown), 8)
        for original, line in zip(figures, shown):
            figure = line.removeprefix("- ")
            self.assertTrue(figure.endswith(" …"))
            self.assertTrue(original.startswith(figure[:-2]))
            self.assertLessEqual(len(canonical(figure)) - 2, 320)
        self.assertIn("## Kennzahlen", digest)
        self.assertIn("- Wörter: 1\n", digest)
        self.assertNotIn("[gekürzt]", digest)

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

    def test_maximum_sections_fit_without_truncating_statistics(self):
        # Ten long keywords and nine figures in three sentences fit the input wire.
        words = [letter * 500 for letter in "abcdefghij"]
        figures = [digit * 1000 for digit in "123456789"]
        chunks = (words[:4], words[4:7], words[7:])
        text = " ".join(
            " ".join(chunk) + " " + " und ".join(figures[index * 3:index * 3 + 3]) + "."
            for index, chunk in enumerate(chunks))
        body = canonical({"payload": {"text": text}})
        self.assertLessEqual(len(body), 16 * 1024)

        digest = self.run_digest_on_wire(text)
        self.assertTrue("## Kennzahlen" in digest, "statistics missing from digest")
        core = digest.split("## Kern in Sätzen\n\n", 1)[1].split("\n\n", 1)[0].splitlines()
        keywords = digest.split("## Schlüsselbegriffe\n\n", 1)[1].split("\n\n", 1)[0].split(", ")
        shown_figures = digest.split("## Zahlen im Text\n\n", 1)[1].split("\n\n", 1)[0].splitlines()
        self.assertEqual(len(core), 3)
        self.assertEqual(len(keywords), 10)
        self.assertEqual(len(shown_figures), 8)
        self.assertTrue(all(len(sentence) <= 320 and sentence.endswith(" …")
                            for sentence in core))
        self.assertTrue(all(len(word.removesuffix(" (1)")) <= 80
                            and word.endswith(" … (1)") for word in keywords))
        for digit, figure in zip("12345678", shown_figures):
            self.assertTrue(figure.startswith(f"- {digit}"))
            self.assertTrue(figure.endswith(" …"))
            self.assertLessEqual(len(figure.removeprefix("- ")), 320)
        self.assertLessEqual(len(digest), 6000)
        self.assertNotIn("[gekürzt]", digest)
        self.assertIn("- Wörter: 16\n", digest)
        self.assertIn("- Sätze: 3\n", digest)

    def test_non_bmp_maximum_sections_fit_the_signed_result_wire(self):
        words = [chr(0x10428 + index) * 32 for index in range(10)]
        figures = [chr(0x1D7CE + index) * 105 for index in range(8)]
        chunks = (words[:4] + figures[:2], words[4:7] + figures[2:5],
                  words[7:] + figures[5:])
        text = ". ".join("; ".join(chunk) for chunk in chunks) + "."
        self.assertEqual(_keywords([word.lower() for word in _words(text)]),
                         [(word, 1) for word in words])
        digest = self.run_digest_on_wire(text)
        core = digest.split("## Kern in Sätzen\n\n", 1)[1].split("\n\n", 1)[0].splitlines()
        keywords = digest.split("## Schlüsselbegriffe\n\n", 1)[1].split("\n\n", 1)[0].split(", ")
        shown_figures = digest.split("## Zahlen im Text\n\n", 1)[1].split("\n\n", 1)[0].splitlines()
        self.assertEqual(len(core), 3)
        self.assertEqual(len(keywords), 10)
        self.assertEqual(len(shown_figures), 8)
        self.assertTrue(all(len(canonical(word.removesuffix(" (1)"))) - 2 <= 80
                            and word.endswith(" … (1)") for word in keywords))
        self.assertTrue(all(len(canonical(line.removeprefix("- "))) - 2 <= 320
                            and line.endswith(" …") for line in shown_figures))
        self.assertLessEqual(len(digest), 6000)
        self.assertIn("- Wörter: 10\n", digest)
        self.assertIn("- Sätze: 3\n", digest)
        self.assertNotIn("[gekürzt]", digest)

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
