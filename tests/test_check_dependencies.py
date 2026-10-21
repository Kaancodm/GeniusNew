"""The dependency watcher without a workflow: unknown is never clean."""

import json
import unittest

from scripts.check_dependencies import advisories, check, parse_pins

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

    def test_summaries_are_truncated(self):
        found = advisories(record({"id": "X", "summary": "y" * 5000}))
        self.assertLessEqual(len(found[0][1]), 200)


if __name__ == "__main__":
    unittest.main()
