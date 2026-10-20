from __future__ import annotations

import json
import io
import os
import resource
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import Mock, patch

from tools import harness2_experiment


_FAKE_DEMO = """import argparse
import json
from pathlib import Path

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--output')
    parser.add_argument('--k')
    parser.add_argument('--mode')
    args = parser.parse_args(argv)
    output = Path(args.output)
    output.mkdir(parents=True)
    (output / 'demo.json').write_text(json.dumps({
        'done': 2, 'failed': 0, 'execution_count': 4, 'editor_calls': 3,
    }))
    return 0
"""


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
        timeout=15,
        env=harness2_experiment._git_environment(),
    ).stdout.strip()


class Harness2ExperimentTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="harness2-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name) / "source"
        self.source.mkdir()
        _git(self.source, "init", "-q")
        _git(self.source, "config", "user.name", "Fixture")
        _git(self.source, "config", "user.email", "fixture@example.invalid")
        _git(self.source, "remote", "add", "origin", harness2_experiment.UPSTREAM_URL)
        package = self.source / "harness2/cli"
        package.mkdir(parents=True)
        (self.source / "harness2/__init__.py").write_text("")
        (package / "__init__.py").write_text("")
        (package / "demo.py").write_text(_FAKE_DEMO)
        _git(self.source, "add", "harness2")
        _git(self.source, "commit", "-qm", "Fixture")
        self.commit = _git(self.source, "rev-parse", "HEAD")
        pinned = patch.object(harness2_experiment, "UPSTREAM_COMMIT", self.commit)
        pinned.start()
        self.addCleanup(pinned.stop)

    def _corrupt_loose_object(self, oid: str, old: bytes, new: bytes) -> None:
        object_path = self.source / ".git/objects" / oid[:2] / oid[2:]
        header, content = zlib.decompress(object_path.read_bytes()).split(b"\0", 1)
        tampered = content.replace(old, new, 1)
        self.assertNotEqual(tampered, content)
        self.assertEqual(len(tampered), len(content))
        object_path.chmod(0o600)
        object_path.write_bytes(zlib.compress(header + b"\0" + tampered))

    def test_runs_committed_demo_even_if_checkout_files_change(self):
        (self.source / "harness2/cli/demo.py").write_text("raise RuntimeError('dirty checkout ran')\n")
        def fake_runner(rootfs: Path, package_root: Path, output_root: Path) -> dict[str, int]:
            self.assertEqual((rootfs / "lib").readlink(), Path("usr/lib"))
            self.assertEqual((package_root / "harness2/cli/demo.py").read_text(), _FAKE_DEMO)
            self.assertEqual(list(output_root.iterdir()), [])
            return {
                "done": 2, "failed": 0, "execution_count": 4, "editor_calls": 3,
            }

        with patch.object(harness2_experiment, "_run_sandboxed_demo", side_effect=fake_runner):
            result = harness2_experiment.run_demo(self.source)
        self.assertEqual(result, {
            "upstream_commit": self.commit,
            "done": 2,
            "failed": 0,
            "execution_count": 4,
            "editor_calls": 3,
        })

    def test_rejects_wrong_origin(self):
        _git(self.source, "remote", "set-url", "origin", "https://example.invalid/harness2.git")
        with self.assertRaisesRegex(harness2_experiment.ExperimentError, "official"):
            harness2_experiment.verify_source(self.source)

    def test_rejects_wrong_head(self):
        (self.source / "new.txt").write_text("new revision\n")
        _git(self.source, "add", "new.txt")
        _git(self.source, "commit", "-qm", "Wrong revision")
        with self.assertRaisesRegex(harness2_experiment.ExperimentError, "HEAD must be"):
            harness2_experiment.verify_source(self.source)

    def test_rejects_nested_checkout_path(self):
        with self.assertRaisesRegex(harness2_experiment.ExperimentError, "checkout root"):
            harness2_experiment.verify_source(self.source / "harness2")

    def test_git_output_is_rejected_at_the_read_limit(self):
        with self.assertRaisesRegex(harness2_experiment.ExperimentError, "limit"):
            harness2_experiment._git_bytes(self.source, "rev-parse", "HEAD", max_bytes=1)

    def test_git_failure_is_rejected_even_with_success_shaped_stdout(self):
        def fake_popen(*arguments, stdout, stderr, **kwargs):
            stdout.write(b"success-shaped output")
            stderr.write(b"failure")
            process = Mock()
            process.wait.return_value = 1
            return process

        with patch.object(harness2_experiment.subprocess, "Popen", side_effect=fake_popen):
            with self.assertRaisesRegex(harness2_experiment.ExperimentError, "failed"):
                harness2_experiment._git_bytes(self.source, "rev-parse", "HEAD")

    def test_sandbox_command_is_closed_to_host_and_network(self):
        command = harness2_experiment._sandbox_command(
            Path("/tmp/rootfs"), Path("/tmp/package"),
            Path("/usr/bin/python3.13"), "/usr/bin/bwrap",
        )
        self.assertIn("--unshare-all", command)
        self.assertIn("--unshare-user", command)
        self.assertIn("--disable-userns", command)
        self.assertEqual(command[command.index("--uid"):command.index("--uid") + 4], [
            "--uid", "65534", "--gid", "65534",
        ])
        self.assertIn("--die-with-parent", command)
        self.assertIn("--clearenv", command)
        self.assertNotIn("--share-net", command)
        self.assertNotIn("--bind", command)
        self.assertNotIn("/home/kaan", command)
        mounts = [command[index:index + 3] for index, item in enumerate(command)
                  if item in ("--ro-bind", "--bind")]
        self.assertEqual(mounts, [
            ["--ro-bind", "/tmp/rootfs", "/"],
            ["--ro-bind", "/usr", "/usr"],
            ["--ro-bind", "/tmp/package", "/source"],
            ["--ro-bind", str(Path(harness2_experiment.__file__).resolve()), "/runner.py"],
        ])
        self.assertEqual(command[command.index("--size"):command.index("--size") + 4], [
            "--size", str(harness2_experiment._OUTPUT_TMPFS_BYTES), "--tmpfs", "/output",
        ])
        self.assertNotIn("/proc", command)
        self.assertNotIn("/dev", command)

    def test_guest_resource_limits_are_inherited_by_sandbox_process(self):
        probe = (
            "import json, resource\n"
            "names = ('RLIMIT_FSIZE', 'RLIMIT_AS', 'RLIMIT_NOFILE', 'RLIMIT_CPU', 'RLIMIT_CORE')\n"
            "print(json.dumps({name: resource.getrlimit(getattr(resource, name))[0] "
            "for name in names}))\n"
        )
        def apply_stricter_caller_limit():
            _, hard = resource.getrlimit(resource.RLIMIT_AS)
            resource.setrlimit(resource.RLIMIT_AS, (128 * 1024 * 1024, hard))
            harness2_experiment._limit_guest_resources()

        result = subprocess.run(
            [sys.executable, "-c", probe],
            preexec_fn=apply_stricter_caller_limit,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        limits = json.loads(result.stdout)
        self.assertLessEqual(limits["RLIMIT_FSIZE"], harness2_experiment._MAX_LOG_BYTES)
        self.assertLessEqual(limits["RLIMIT_AS"], 128 * 1024 * 1024)
        self.assertLessEqual(limits["RLIMIT_NOFILE"], 64)
        self.assertLessEqual(limits["RLIMIT_CPU"], 60)
        self.assertEqual(limits["RLIMIT_CORE"], 0)

    def test_git_subprocess_limits_keep_stricter_caller_limit(self):
        probe = (
            "import json, resource\n"
            "names = ('RLIMIT_FSIZE', 'RLIMIT_AS', 'RLIMIT_NOFILE', 'RLIMIT_CPU', 'RLIMIT_CORE')\n"
            "print(json.dumps({name: resource.getrlimit(getattr(resource, name))[0] "
            "for name in names}))\n"
        )

        def apply_stricter_caller_limit():
            _, hard = resource.getrlimit(resource.RLIMIT_AS)
            resource.setrlimit(resource.RLIMIT_AS, (128 * 1024 * 1024, hard))
            harness2_experiment._limit_git_file_size(100)

        result = subprocess.run(
            [sys.executable, "-c", probe],
            preexec_fn=apply_stricter_caller_limit,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        limits = json.loads(result.stdout)
        self.assertLessEqual(limits["RLIMIT_FSIZE"], 101)
        self.assertLessEqual(limits["RLIMIT_AS"], 128 * 1024 * 1024)
        self.assertLessEqual(limits["RLIMIT_NOFILE"], 64)
        self.assertLessEqual(limits["RLIMIT_CPU"], 5)
        self.assertEqual(limits["RLIMIT_CORE"], 0)

    def test_missing_sandbox_is_rejected(self):
        with patch.object(harness2_experiment.shutil, "which", return_value=None):
            with self.assertRaisesRegex(harness2_experiment.ExperimentError, "unavailable"):
                harness2_experiment._run_sandboxed_demo(
                    Path("/tmp/rootfs"), Path("/tmp/package"), Path("/tmp/output")
                )

    def test_unsupported_python_and_outside_interpreter_are_rejected_before_spawn(self):
        output = Path(self.temporary.name) / "output"
        output.mkdir()
        with (patch.object(harness2_experiment.shutil, "which", return_value="/usr/bin/bwrap"),
              patch.object(harness2_experiment.subprocess, "Popen",
                           side_effect=AssertionError("sandbox launched"))):
            with patch.object(harness2_experiment.sys, "version_info", (3, 11)):
                with self.assertRaisesRegex(harness2_experiment.ExperimentError, "3.12 or 3.13"):
                    harness2_experiment._run_sandboxed_demo(
                        Path("/tmp/rootfs"), Path("/tmp/package"), output
                    )
            outside = Path(self.temporary.name) / "python"
            outside.write_bytes(b"fake")
            with (patch.object(harness2_experiment.sys, "version_info", (3, 13)),
                  patch.object(harness2_experiment.sys, "executable", str(outside))):
                with self.assertRaisesRegex(harness2_experiment.ExperimentError, "outside"):
                    harness2_experiment._run_sandboxed_demo(
                        Path("/tmp/rootfs"), Path("/tmp/package"), output
                    )

    def test_sandbox_failure_is_rejected_even_with_success_shaped_stdout(self):
        output = Path(self.temporary.name) / "output"
        output.mkdir()
        payload = json.dumps({
            "done": 2, "failed": 0, "execution_count": 4, "editor_calls": 3,
        }).encode()

        def fake_popen(*arguments, stdout, stderr, **kwargs):
            stdout.write(payload)
            stderr.write(b"child failed")
            process = Mock()
            process.wait.return_value = 1
            return process

        with (patch.object(harness2_experiment.shutil, "which", return_value="/usr/bin/bwrap"),
              patch.object(harness2_experiment.sys, "version_info", (3, 13)),
              patch.object(harness2_experiment.sys, "executable", "/usr/bin/python3"),
              patch.object(harness2_experiment.subprocess, "Popen", side_effect=fake_popen)):
            with self.assertRaisesRegex(harness2_experiment.ExperimentError, "child failed"):
                harness2_experiment._run_sandboxed_demo(
                    Path("/tmp/rootfs"), Path("/tmp/package"), output
                )

    def test_guest_upstream_failure_does_not_emit_a_report(self):
        report = {"done": 2, "failed": 0, "execution_count": 4, "editor_calls": 3}
        with (patch.object(harness2_experiment.resource, "getrlimit", return_value=(8, 64)),
              patch.object(harness2_experiment.resource, "setrlimit") as setrlimit,
              patch.object(Path, "open", return_value=io.BytesIO(b"child failed")),
              patch.object(harness2_experiment.subprocess, "run",
                           return_value=subprocess.CompletedProcess([], 1)),
              patch.object(harness2_experiment, "_read_demo_report", return_value=report)):
            with self.assertRaisesRegex(harness2_experiment.ExperimentError, "child failed"):
                harness2_experiment._guest_demo()
            setrlimit.assert_called_once_with(resource.RLIMIT_NPROC, (8, 8))

    def test_git_tree_rejects_a_symlink(self):
        (self.source / "harness2/cli/external.py").symlink_to("/tmp/outside.py")
        _git(self.source, "add", "harness2/cli/external.py")
        _git(self.source, "commit", "-qm", "Symlink in source")
        with patch.object(harness2_experiment, "UPSTREAM_COMMIT", _git(self.source, "rev-parse", "HEAD")):
            with self.assertRaisesRegex(harness2_experiment.ExperimentError, "unexpected"):
                harness2_experiment._extract_package(self.source, Path(self.temporary.name) / "copy")

    def test_git_tree_rejects_an_oversized_blob(self):
        (self.source / "harness2/large.bin").write_bytes(
            b"x" * (harness2_experiment._MAX_FILE_BYTES + 1)
        )
        _git(self.source, "add", "harness2/large.bin")
        _git(self.source, "commit", "-qm", "Oversized source")
        with patch.object(harness2_experiment, "UPSTREAM_COMMIT", _git(self.source, "rev-parse", "HEAD")):
            with self.assertRaisesRegex(harness2_experiment.ExperimentError, "limit"):
                harness2_experiment._extract_package(self.source, Path(self.temporary.name) / "copy")

    def test_git_tree_rejects_missing_terminator(self):
        original = harness2_experiment._read_object
        root_oid = _git(self.source, "rev-parse", "HEAD^{tree}")

        def without_final_oid_byte(source, kind, oid, max_bytes):
            result = original(source, kind, oid, max_bytes)
            return result[:-1] if kind == "tree" and oid == root_oid else result

        with patch.object(harness2_experiment, "_read_object", side_effect=without_final_oid_byte):
            with self.assertRaisesRegex(harness2_experiment.ExperimentError, "invalid pinned tree"):
                harness2_experiment._extract_package(
                    self.source, Path(self.temporary.name) / "truncated-copy"
                )

    def test_raw_tree_parser_rejects_path_traversal(self):
        raw = b"100644 ..\0" + bytes.fromhex("a" * 40)
        with self.assertRaisesRegex(harness2_experiment.ExperimentError, "invalid pinned tree"):
            harness2_experiment._tree_entries(raw)

    def test_commit_requires_an_explicit_tree_header(self):
        root_oid = _git(self.source, "rev-parse", "HEAD^{tree}")
        original = harness2_experiment._read_object

        def wrong_header(source, kind, oid, max_bytes):
            if kind == "commit":
                return b"nope " + root_oid.encode() + b"\n"
            return original(source, kind, oid, max_bytes)

        with patch.object(harness2_experiment, "_read_object", side_effect=wrong_header):
            with self.assertRaisesRegex(harness2_experiment.ExperimentError, "root tree"):
                harness2_experiment._extract_package(self.source, None)

    def test_missing_package_tree_is_rejected(self):
        _git(self.source, "rm", "-qr", "harness2")
        _git(self.source, "commit", "-qm", "No package")
        with patch.object(harness2_experiment, "UPSTREAM_COMMIT", _git(self.source, "rev-parse", "HEAD")):
            with self.assertRaisesRegex(harness2_experiment.ExperimentError, "missing"):
                harness2_experiment._extract_package(self.source, None)

    def test_excessively_deep_package_tree_is_rejected(self):
        commit_oid = "a" * 40
        root_oid = "b" * 40
        blob_oid = "c" * 40
        tree_oids = [f"{number + 1:040x}" for number in range(harness2_experiment._MAX_TREES + 1)]
        objects = {
            ("commit", commit_oid): b"tree " + root_oid.encode() + b"\n",
            ("tree", root_oid): b"40000 harness2\0" + bytes.fromhex(tree_oids[0]),
            ("blob", blob_oid): b"x",
        }
        for index, oid in enumerate(tree_oids):
            if index + 1 < len(tree_oids):
                objects[("tree", oid)] = b"40000 sub\0" + bytes.fromhex(tree_oids[index + 1])
            else:
                objects[("tree", oid)] = b"100644 final.py\0" + bytes.fromhex(blob_oid)

        def fake_object(source, kind, oid, max_bytes):
            return objects[(kind, oid)]

        with (patch.object(harness2_experiment, "UPSTREAM_COMMIT", commit_oid),
              patch.object(harness2_experiment, "_read_object", side_effect=fake_object)):
            with self.assertRaisesRegex(harness2_experiment.ExperimentError, "too many trees"):
                harness2_experiment._extract_package(self.source, None)

    def test_empty_package_tree_is_rejected(self):
        commit_oid = "a" * 40
        root_oid = "b" * 40
        package_oid = "c" * 40
        objects = {
            ("commit", commit_oid): b"tree " + root_oid.encode() + b"\n",
            ("tree", root_oid): b"40000 harness2\0" + bytes.fromhex(package_oid),
            ("tree", package_oid): b"",
        }
        with (patch.object(harness2_experiment, "UPSTREAM_COMMIT", commit_oid),
              patch.object(harness2_experiment, "_read_object",
                           side_effect=lambda source, kind, oid, max_bytes: objects[(kind, oid)])):
            with self.assertRaisesRegex(harness2_experiment.ExperimentError, "empty"):
                harness2_experiment._extract_package(self.source, None)

    def test_git_tree_rejects_too_many_files(self):
        for number in range(harness2_experiment._MAX_FILES):
            (self.source / "harness2" / f"extra-{number:03}.txt").write_text("x")
        _git(self.source, "add", "harness2")
        _git(self.source, "commit", "-qm", "Too many files")
        with patch.object(harness2_experiment, "UPSTREAM_COMMIT", _git(self.source, "rev-parse", "HEAD")):
            with self.assertRaisesRegex(harness2_experiment.ExperimentError, "too many files"):
                harness2_experiment._extract_package(
                    self.source, Path(self.temporary.name) / "many-copy"
                )

    def test_git_tree_rejects_too_many_total_bytes(self):
        for number in range(5):
            (self.source / "harness2" / f"large-{number}.bin").write_bytes(b"x" * 900_000)
        _git(self.source, "add", "harness2")
        _git(self.source, "commit", "-qm", "Too much package data")
        with patch.object(harness2_experiment, "UPSTREAM_COMMIT", _git(self.source, "rev-parse", "HEAD")):
            with self.assertRaisesRegex(harness2_experiment.ExperimentError, "package exceeds"):
                harness2_experiment._extract_package(
                    self.source, Path(self.temporary.name) / "large-copy"
                )

    def test_local_attributes_cannot_filter_pinned_blobs(self):
        marker = Path(self.temporary.name) / "filter-ran"
        filter_script = Path(self.temporary.name) / "filter.py"
        filter_script.write_text(
            "import pathlib, sys\n"
            f"pathlib.Path({str(marker)!r}).write_text('ran')\n"
            "sys.stdout.buffer.write(sys.stdin.buffer.read())\n"
        )
        attributes = self.source / ".git/info/attributes"
        attributes.write_text("harness2/cli/demo.py filter=evil export-subst\n")
        _git(self.source, "config", "filter.evil.smudge", f"python3 {filter_script}")
        copy = Path(self.temporary.name) / "copy"
        harness2_experiment._extract_package(self.source, copy)
        self.assertEqual((copy / "harness2/cli/demo.py").read_text(), _FAKE_DEMO)
        self.assertFalse(marker.exists())

    def test_tampered_loose_blob_is_rejected_even_if_git_returns_its_bytes(self):
        oid = _git(self.source, "rev-parse", "HEAD:harness2/cli/demo.py")
        self._corrupt_loose_object(oid, b"'done': 2", b"'done': 9")
        self.assertIn("'done': 9", _git(self.source, "cat-file", "blob", oid))
        with self.assertRaisesRegex(harness2_experiment.ExperimentError, "blob hash"):
            harness2_experiment._extract_package(
                self.source, Path(self.temporary.name) / "tampered-copy"
            )
        with self.assertRaisesRegex(harness2_experiment.ExperimentError, "blob hash"):
            harness2_experiment.verify_source(self.source)

    def test_tampered_commit_and_tree_are_rejected_by_own_hashes(self):
        self._corrupt_loose_object(self.commit, b"Fixture", b"Fixsure")
        with self.assertRaisesRegex(harness2_experiment.ExperimentError, "commit hash"):
            harness2_experiment.verify_source(self.source)

    def test_tampered_root_tree_is_rejected_by_own_hash(self):
        root_oid = _git(self.source, "rev-parse", "HEAD^{tree}")
        self._corrupt_loose_object(root_oid, b"harness2", b"harness3")
        with self.assertRaisesRegex(harness2_experiment.ExperimentError, "tree hash"):
            harness2_experiment.verify_source(self.source)

    def test_unrelated_corrupt_object_does_not_trigger_a_repository_wide_scan(self):
        unrelated = self.source / "unrelated.txt"
        unrelated.write_bytes(b"unrelated")
        oid = _git(self.source, "hash-object", "-w", str(unrelated))
        self._corrupt_loose_object(oid, b"unrelated", b"different")
        self.assertEqual(harness2_experiment.verify_source(self.source), self.source)

    def test_report_rejects_symlinks_and_invalid_counters(self):
        output = Path(self.temporary.name) / "output"
        demo = output / "demo"
        demo.mkdir(parents=True)
        target = Path(self.temporary.name) / "target.json"
        target.write_text(json.dumps({
            "done": 2, "failed": 0, "execution_count": 4, "editor_calls": 3,
        }))
        link = demo / "demo.json"
        link.symlink_to(target)
        with self.assertRaisesRegex(harness2_experiment.ExperimentError, "unavailable"):
            harness2_experiment._read_demo_report(output)
        link.unlink()
        demo.rmdir()
        demo.symlink_to(target.parent, target_is_directory=True)
        with self.assertRaisesRegex(harness2_experiment.ExperimentError, "unavailable"):
            harness2_experiment._read_demo_report(output)
        demo.unlink()
        demo.mkdir()
        report = demo / "demo.json"
        report.write_text(json.dumps({
            "done": True, "failed": 0, "execution_count": 4, "editor_calls": 3,
        }))
        with self.assertRaisesRegex(harness2_experiment.ExperimentError, "invalid counters"):
            harness2_experiment._read_demo_report(output)
        report.write_bytes(b"x" * (harness2_experiment._MAX_REPORT_BYTES + 1))
        with self.assertRaisesRegex(harness2_experiment.ExperimentError, "bounded"):
            harness2_experiment._read_demo_report(output)

    def test_fifo_report_is_rejected_without_blocking(self):
        output = Path(self.temporary.name) / "output"
        demo = output / "demo"
        demo.mkdir(parents=True)
        os.mkfifo(demo / "demo.json")
        probe = (
            "import sys\n"
            "from pathlib import Path\n"
            "from tools import harness2_experiment as h\n"
            "try:\n"
            "    h._read_demo_report(Path(sys.argv[1]))\n"
            "except h.ExperimentError:\n"
            "    print('rejected')\n"
            "else:\n"
            "    raise SystemExit('FIFO accepted')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe, str(output)],
            cwd=Path(__file__).resolve().parents[1],
            env=harness2_experiment._git_environment(),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "rejected")

    def test_report_rejects_deep_json_without_recursion_crash(self):
        with patch.object(harness2_experiment.json, "loads", side_effect=RecursionError):
            with self.assertRaisesRegex(harness2_experiment.ExperimentError, "invalid JSON"):
                harness2_experiment._parse_demo_report(b"[]")
        with self.assertRaisesRegex(harness2_experiment.ExperimentError, "invalid JSON"):
            harness2_experiment._parse_demo_report(b"\xff")

    def test_report_rejects_large_valid_json_wrong_shape_and_incomplete_demo(self):
        valid = {"done": 2, "failed": 0, "execution_count": 4, "editor_calls": 3}
        padded = {**valid, "padding": "x" * harness2_experiment._MAX_REPORT_BYTES}
        with self.assertRaisesRegex(harness2_experiment.ExperimentError, "exceeds"):
            harness2_experiment._parse_demo_report(json.dumps(padded).encode())
        with self.assertRaisesRegex(harness2_experiment.ExperimentError, "not an object"):
            harness2_experiment._parse_demo_report(b"[]")
        with self.assertRaisesRegex(harness2_experiment.ExperimentError, "did not complete"):
            harness2_experiment._parse_demo_report(json.dumps({**valid, "done": 1}).encode())


if __name__ == "__main__":
    unittest.main()
