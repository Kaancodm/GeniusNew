from __future__ import annotations

import io
import subprocess
import tarfile
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

    def test_demo_has_no_model_or_cloud_environment(self):
        original = harness2_experiment._DEMO_CODE
        probe = (
            "import os, sys, json; from pathlib import Path; "
            "assert 'GOOGLE_CLOUD_PROJECT' not in os.environ; "
            "assert 'OPENAI_API_KEY' not in os.environ; "
            "sys.path.insert(0, sys.argv[1]); "
            "from harness2.cli.demo import main; raise SystemExit(main(sys.argv[2:]))"
        )
        with patch.object(harness2_experiment, "_DEMO_CODE", probe), patch.dict(
            "os.environ", {"GOOGLE_CLOUD_PROJECT": "sensitive", "OPENAI_API_KEY": "sensitive"}
        ):
            result = harness2_experiment.run_demo(self.source)
        self.assertEqual(result["done"], 2)
        self.assertEqual(harness2_experiment._DEMO_CODE, original)

    def test_archive_rejects_a_symlink(self):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:") as bundle:
            member = tarfile.TarInfo("harness2/cli/demo.py")
            member.type = tarfile.SYMTYPE
            member.linkname = "/tmp/outside.py"
            bundle.addfile(member)
        with self.assertRaisesRegex(harness2_experiment.ExperimentError, "unexpected"):
            harness2_experiment._extract_package(archive.getvalue(), Path(self.temporary.name))


if __name__ == "__main__":
    unittest.main()
