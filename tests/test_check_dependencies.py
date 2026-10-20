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

    def test_orphaned_or_incomplete_hash_continuations_are_unknown_before_fetch(self):
        pin = "sample==1.0"
        hashed = "    --hash=sha256:abc"
        malformed = (
            hashed,
            pin + "\n" + hashed,
            pin + "\n# comment\n" + hashed,
            pin + " \\\n" + hashed + "\n" + hashed,
            pin + " # comment \\\n" + hashed,
            pin + " \\\n# comment\n" + hashed,
            pin + " \\\n\n" + hashed,
            pin + " \\\nother==2.0",
            pin + " \\\n" + hashed + " \\\nother==2.0",
            pin + " \\",
            pin + " \\\n" + hashed + " \\",
            pin + " \\\n" + hashed + " \\\n\n",
            pin + ' ; python_version < "3.13" \\\n' + hashed + "\n" + hashed,
            pin + " \\\n    --hash=sha512:abc",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requirements.txt"
            for text in malformed:
                with self.subTest(text=text):
                    with self.assertRaisesRegex(ValueError, "unsupported"):
                        parse_pins(text)
                    path.write_text(text, encoding="utf-8")
                    err, out, calls = io.StringIO(), io.StringIO(), []
                    with redirect_stderr(err), redirect_stdout(out):
                        code = main(
                            [str(path)], fetch=lambda url: calls.append(url) or record())
                    self.assertEqual(code, 2)
                    self.assertIn("UNKNOWN", err.getvalue())
                    self.assertEqual(out.getvalue(), "")
                    self.assertEqual(calls, [])

    def test_valid_hash_chains_and_markers_keep_every_pin_in_the_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requirements.txt"
            for marker in ("", ' ; python_version < "3.13"'):
                for hashes in (1, 2, 4):
                    with self.subTest(marker=marker, hashes=hashes):
                        lines = ["sample==1.0" + marker + " \\"]
                        lines += ["    --hash=sha256:abc \\"] * (hashes - 1)
                        lines += ["    --hash=sha256:def", "", "# next pin",
                                  "other==2.0 # comment \\"]
                        text = "\n".join(lines) + "\n"
                        self.assertEqual(parse_pins(text),
                                         [("sample", "1.0"), ("other", "2.0")])
                        path.write_text(text, encoding="utf-8")
                        calls = []
                        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                            code = main([str(path)],
                                        fetch=lambda url: calls.append(url) or record())
                        self.assertEqual(code, 0)
                        self.assertEqual(len(calls), 2)
                        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                            code = main([str(path)],
                                        fetch=lambda _: record({"id": "GHSA-demo"}))
                        self.assertEqual(code, 1)

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

    def test_invalid_or_unsupported_markers_are_unknown_before_fetch(self):
        markers = (
            "", " ", "garbage", "# comment", "\\",
            'python_version', 'python_version <', 'python_version < 3.13',
            'python_version < "3.13', 'python_version = "3.13"',
            'unknown_variable == "value"', 'python_version < "3.13" garbage',
            'python_version < "3.13" ; os_name == "posix"',
            'python_version < "3.13" and', 'or os_name == "posix"',
            '()', '(os_name == "posix"', 'os_name == "posix")',
            '(os_name == "posix") (sys_platform == "linux")',
            'os_name == "posix" and or sys_platform == "linux"',
            'os_name == "posix" # ignored garbage',
            'os_name == "posix" \\ garbage',
            'os_name == "posix\\"', 'os_name == "pösix"',
            '"posix" == "posix"', 'os_name == sys_platform',
            'os_name in"posix"', 'os_name == "posix" android == "x"',
            'extra == "test"', 'extras == "test"',
            'dependency_groups == "test"',
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requirements.txt"
            for marker in markers:
                with self.subTest(marker=marker):
                    text = 'sample==1.0 ; ' + marker + '\n'
                    with self.assertRaisesRegex(ValueError, "unsupported"):
                        parse_pins(text)
                    path.write_text(text, encoding="utf-8")
                    err, out = io.StringIO(), io.StringIO()
                    calls = []
                    with redirect_stderr(err), redirect_stdout(out):
                        result = main(
                            [str(path)], fetch=lambda url: calls.append(url) or record())
                    self.assertEqual(result, 2)
                    self.assertIn("UNKNOWN", err.getvalue())
                    self.assertEqual(out.getvalue(), "")
                    self.assertEqual(calls, [])

    def test_valid_marker_grammar_keeps_every_pin_in_the_audit(self):
        markers = (
            'python_version < "3.13"', "python_full_version >= '3.11.0'",
            'os_name == "posix"', 'sys_platform != "win32"',
            'platform_release > "1"', 'platform_system <= "Windows"',
            'platform_version === "custom"', 'platform_machine in "arm64,x86_64"',
            'platform_python_implementation not in "PyPy"',
            'implementation_name == "cpython"', 'implementation_version ~= "3.11"',
            '"linux" == sys_platform',
            '(python_version < "3.13" and os_name == "posix") or '
            '(sys_platform == "win32" and (platform_machine == "ARM64"))',
            'os_name=="posix" and sys_platform=="linux" and python_version>="3"',
            'os_name == "a#b;c"',
            '(' * 1500 + 'os_name == "posix"' + ')' * 1500,
        )
        for marker in markers:
            for suffix in ("", " \\\n    --hash=sha256:abc"):
                with self.subTest(marker=marker, suffix=suffix):
                    pins = parse_pins('sample==1.0 ; ' + marker + suffix + '\n')
                    self.assertEqual(pins, [("sample", "1.0")])
                    calls = []
                    code, _ = check(pins, fetch=lambda url: calls.append(url) or record())
                    self.assertEqual(code, 0)
                    self.assertEqual(len(calls), 1)
                    code, _ = check(pins, fetch=lambda _: record({"id": "GHSA-demo"}))
                    self.assertEqual(code, 1)

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

    def test_malformed_withdrawal_is_unknown_never_clean(self):
        malformed = ("", " ", "not-a-timestamp", "2026-10-01",
                     "2026-10-01T00:00:00", "2026-02-30T00:00:00Z",
                     "2026-10-01T24:00:00Z", "2026-10-01T00:00:00+00:60",
                     "2026-10-01T00:00:00Z\n", True, 0, [], {})
        for withdrawn in malformed:
            with self.subTest(withdrawn=withdrawn):
                code, lines = check([("sample", "1.0")], fetch=lambda _: record(
                    {"id": "GHSA-demo", "withdrawn": withdrawn}))
                self.assertEqual(code, 2)
                self.assertIn("UNKNOWN", lines[-1])
                self.assertNotIn("no known advisories", " ".join(lines))

    def test_withdrawal_timestamp_accepts_offsets_and_fractional_seconds(self):
        for withdrawn in ("2026-10-01T00:00:00Z",
                          "2026-10-01T02:30:00.123456789+02:30",
                          "2026-10-01t00:00:00z"):
            with self.subTest(withdrawn=withdrawn):
                code, _ = check([("sample", "1.0")], fetch=lambda _: record(
                    {"id": "GHSA-demo", "withdrawn": withdrawn}))
                self.assertEqual(code, 0)

    def test_missing_or_null_withdrawal_keeps_an_advisory_active(self):
        for entry in ({"id": "GHSA-demo"},
                      {"id": "GHSA-demo", "withdrawn": None}):
            with self.subTest(entry=entry):
                code, lines = check([("sample", "1.0")],
                                    fetch=lambda _: record(entry))
                self.assertEqual(code, 1)
                self.assertIn("GHSA-demo", lines[0])

    def test_summaries_are_truncated(self):
        found = advisories(record({"id": "X", "summary": "y" * 5000}))
        self.assertLessEqual(len(found[0][1]), 200)


if __name__ == "__main__":
    unittest.main()
