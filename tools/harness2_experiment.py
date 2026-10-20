"""Run the pinned Harness² local demo without installing it into GeniusNew."""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


UPSTREAM_URL = "https://github.com/google-research/harness2.git"
UPSTREAM_COMMIT = "1cfe254e8d25b15f37048c3730134686abe60335"
_DEMO_CODE = (
    "import sys; "
    "sys.path.insert(0, sys.argv[1]); "
    "from harness2.cli.demo import main; "
    "raise SystemExit(main(sys.argv[2:]))"
)


class ExperimentError(Exception):
    """The source proof or the isolated demo run failed."""


def _git(source: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(source), *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExperimentError(f"git unavailable: {exc}") from exc
    if result.returncode:
        raise ExperimentError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _git_environment() -> dict[str, str]:
    """Keep inherited Git overrides from substituting a different object database."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    return environment


def verify_source(source: Path) -> Path:
    """Require the official remote and exact pinned checkout before reading objects."""
    try:
        source = source.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ExperimentError(f"source unavailable: {exc}") from exc
    if not source.is_dir():
        raise ExperimentError("source must be a directory")
    if Path(_git(source, "rev-parse", "--show-toplevel")).resolve() != source:
        raise ExperimentError("source must be the checkout root")
    if _git(source, "remote", "get-url", "origin") != UPSTREAM_URL:
        raise ExperimentError("source origin is not the official Harness² repository")
    if _git(source, "rev-parse", "HEAD") != UPSTREAM_COMMIT:
        raise ExperimentError(f"source HEAD must be {UPSTREAM_COMMIT}")
    return source


def _extract_package(archive: bytes, destination: Path) -> None:
    """Materialize only tracked package files from the pinned Git object."""
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            for member in bundle:
                path = PurePosixPath(member.name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or not path.parts
                    or path.parts[0] != "harness2"
                    or not (member.isdir() or member.isfile())
                ):
                    raise ExperimentError("unexpected archive entry")
                target = destination.joinpath(*path.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                content = bundle.extractfile(member)
                if content is None:
                    raise ExperimentError("archive file is unreadable")
                target.write_bytes(content.read())
    except (OSError, tarfile.TarError) as exc:
        raise ExperimentError(f"cannot extract pinned package: {exc}") from exc


def run_demo(source: Path) -> dict[str, int | str]:
    """Exercise both deterministic recursion modes in a disposable directory."""
    source = verify_source(source)
    try:
        archive = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(source), "archive", "--format=tar",
             UPSTREAM_COMMIT, "harness2"],
            capture_output=True,
            timeout=30,
            check=False,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExperimentError(f"cannot read pinned archive: {exc}") from exc
    if archive.returncode:
        raise ExperimentError(f"cannot read pinned archive: {archive.stderr.decode(errors='replace')}")

    with tempfile.TemporaryDirectory(prefix="geniusnew-harness2-") as temporary:
        root = Path(temporary)
        package_root = root / "source"
        package_root.mkdir()
        _extract_package(archive.stdout, package_root)
        home = root / "home"
        home.mkdir()
        output = root / "demo"
        environment = {
            "PATH": os.defpath,
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "TMPDIR": str(root),
            "LANG": "C.UTF-8",
        }
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-c", _DEMO_CODE, str(package_root),
                 "--output", str(output), "--k", "1", "--mode", "both"],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExperimentError(f"demo did not finish: {exc}") from exc
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip()[-1000:]
            raise ExperimentError(f"demo failed: {detail}")
        try:
            report = json.loads((output / "demo.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ExperimentError(f"demo report unavailable: {exc}") from exc
        if report.get("done") != 2 or report.get("failed") != 0:
            raise ExperimentError("demo did not complete both tasks")
        return {
            "upstream_commit": UPSTREAM_COMMIT,
            "done": report["done"],
            "failed": report["failed"],
            "execution_count": report.get("execution_count", 0),
            "editor_calls": report.get("editor_calls", 0),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "demo"))
    parser.add_argument("--source", type=Path, required=True, help="local Harness² Git checkout")
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            verify_source(args.source)
            print(json.dumps({"origin": UPSTREAM_URL, "upstream_commit": UPSTREAM_COMMIT,
                              "status": "verified"}, sort_keys=True))
        else:
            print(json.dumps(run_demo(args.source), sort_keys=True))
    except ExperimentError as exc:
        parser.exit(2, f"Harness² experiment: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
