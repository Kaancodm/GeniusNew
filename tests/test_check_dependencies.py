"""The dependency watcher without a workflow: unknown is never clean."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts.check_dependencies import advisories, check, main, parse_pins

REQUIREMENTS = """\
# a comment
cryptography==50.0.1 \\
    --hash=sha256:abc
typing-extensions==4.16.0 ; python_version < "3.13" \\
    --hash=sha256:def
psycopg==3.3.3
"""


def record(*entries):
    return json.dumps({"vulnerabilities": list(entries)}).encode()


class CheckDependenciesTest(unittest.TestCase):
    def test_pins_are_read_through_hashes_and_markers(self):
        self.assertEqual(parse_pins(REQUIREMENTS),
                         [("cryptography", "50.0.1"),
                          ("typing-extensions", "4.16.0"),
                          ("psycopg", "3.3.3")])

    def test_a_clean_set_passes_and_says_for_how_many(self):
        code, lines = check(parse_pins(REQUIREMENTS), fetch=lambda url: record())
        self.assertEqual(code, 0)
        self.assertIn("3 pinned packages", lines[-1])

    def test_an_advisory_fails_and_is_named(self):
        def fetch(url):
            if "cryptography" in url:
                return record({"id": "GHSA-demo", "summary": "bad thing\nin detail"})
            return record()

        code, lines = check(parse_pins(REQUIREMENTS), fetch=fetch)
        self.assertEqual(code, 1)
        self.assertIn("GHSA-demo", lines[0])
        self.assertIn("bad thing in detail", lines[0])  # one line, bounded

    def test_an_unreachable_index_is_unknown_never_clean(self):
        def fetch(url):
            raise OSError("no route")

        code, lines = check(parse_pins(REQUIREMENTS), fetch=fetch)
        self.assertEqual(code, 2)
        self.assertIn("UNKNOWN", lines[-1])

    def test_a_malformed_record_is_unknown(self):
        code, _ = check([("thing", "1.0")], fetch=lambda url: b"{}")
        self.assertEqual(code, 2)

    def test_no_pins_is_unknown_not_a_pass(self):
        code, _ = check([], fetch=lambda url: record())
        self.assertEqual(code, 2)

    def test_unknown_requirement_syntax_cannot_be_silently_ignored(self):
        for unsupported in ("-r extra-requirements.txt", "package~=1.0",
                            "--extra-index-url https://example.com/simple"):
            with self.subTest(unsupported=unsupported):
                with self.assertRaisesRegex(ValueError, "unsupported"):
                    parse_pins("cryptography==50.0.1\n" + unsupported + "\n")

    def test_unknown_requirement_syntax_exits_two_from_the_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requirements.txt"
            path.write_text("cryptography==50.0.1\n-r extra.txt\n", encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                result = main([str(path)], fetch=lambda _: record())
            self.assertEqual(result, 2)
            self.assertIn("UNKNOWN", err.getvalue())

    def test_a_null_advisory_entry_must_be_unknown(self):
        code, lines = check([("sample", "1.0")],
                            fetch=lambda _: record(None))
        self.assertEqual(code, 2)
        self.assertIn("UNKNOWN", lines[-1])

    def test_a_withdrawn_advisory_is_not_an_active_vulnerability(self):
        withdrawn = {"id": "GHSA-retracted", "summary": "withdrawn",
                     "withdrawn": "2026-10-01T00:00:00Z"}
        code, lines = check([("sample", "1.0")],
                            fetch=lambda _: record(withdrawn))
        self.assertEqual(code, 0)
        self.assertNotIn("GHSA-retracted", " ".join(lines))

    def test_summaries_are_truncated(self):
        found = advisories(record({"id": "X", "summary": "y" * 5000}))
        self.assertLessEqual(len(found[0][1]), 200)


if __name__ == "__main__":
    unittest.main()
