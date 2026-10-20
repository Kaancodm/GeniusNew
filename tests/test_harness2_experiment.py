from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_runs_committed_demo_even_if_checkout_files_change(self):
        (self.source / "harness2/cli/demo.py").write_text("raise RuntimeError('dirty checkout ran')\n")
        def fake_runner(rootfs: Path, package_root: Path, output_root: Path) -> None:
            self.assertEqual((rootfs / "lib").readlink(), Path("usr/lib"))
            self.assertEqual((package_root / "harness2/cli/demo.py").read_text(), _FAKE_DEMO)
            report = output_root / "demo/demo.json"
            report.parent.mkdir(parents=True)
            report.write_text(json.dumps({
                "done": 2, "failed": 0, "execution_count": 4, "editor_calls": 3,
            }))

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

    def test_sandbox_command_is_closed_to_host_and_network(self):
        command = harness2_experiment._sandbox_command(
            Path("/tmp/rootfs"), Path("/tmp/package"), Path("/tmp/output"),
            Path("/usr/bin/python3.13"), "/usr/bin/bwrap",
        )
        self.assertIn("--unshare-all", command)
        self.assertIn("--unshare-user", command)
        self.assertIn("--disable-userns", command)
        self.assertIn("--die-with-parent", command)
        self.assertIn("--clearenv", command)
        self.assertNotIn("--share-net", command)
        self.assertNotIn("/home/kaan", command)
        mounts = [command[index:index + 3] for index, item in enumerate(command)
                  if item in ("--ro-bind", "--bind")]
        self.assertEqual(mounts, [
            ["--ro-bind", "/tmp/rootfs", "/"],
            ["--ro-bind", "/usr", "/usr"],
            ["--ro-bind", "/tmp/package", "/source"],
            ["--bind", "/tmp/output", "/output"],
        ])
        self.assertNotIn("/proc", command)
        self.assertNotIn("/dev", command)

    def test_missing_sandbox_is_rejected(self):
        with patch.object(harness2_experiment.shutil, "which", return_value=None):
            with self.assertRaisesRegex(harness2_experiment.ExperimentError, "unavailable"):
                harness2_experiment._run_sandboxed_demo(
                    Path("/tmp/rootfs"), Path("/tmp/package"), Path("/tmp/output")
                )

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
            with self.assertRaisesRegex(harness2_experiment.ExperimentError, "unexpected"):
                harness2_experiment._extract_package(self.source, Path(self.temporary.name) / "copy")

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


if __name__ == "__main__":
    unittest.main()
