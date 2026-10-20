"""Run the pinned Harness² local demo without installing it into GeniusNew."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath


UPSTREAM_URL = "https://github.com/google-research/harness2.git"
UPSTREAM_COMMIT = "1cfe254e8d25b15f37048c3730134686abe60335"
_MAX_FILES = 128
_MAX_FILE_BYTES = 1024 * 1024
_MAX_PACKAGE_BYTES = 4 * 1024 * 1024
_MAX_TREE_BYTES = 64 * 1024
_MAX_REPORT_BYTES = 64 * 1024
_DEMO_CODE = (
    "import sys; "
    "sys.path.insert(0, sys.argv[1]); "
    "from harness2.cli.demo import main; "
    "raise SystemExit(main(sys.argv[2:]))"
)


class ExperimentError(Exception):
    """The source proof or the temporary demo run failed."""


def _git_bytes(source: Path, *args: str, max_bytes: int = 16 * 1024) -> bytes:
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(source), *args],
            capture_output=True,
            timeout=15,
            check=False,
            stdin=subprocess.DEVNULL,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExperimentError(f"git unavailable: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode(errors="replace")[-500:].strip()
        raise ExperimentError(f"git {' '.join(args)} failed: {detail}")
    if len(result.stdout) > max_bytes:
        raise ExperimentError("git object exceeds the experiment limit")
    return result.stdout


def _git(source: Path, *args: str) -> str:
    try:
        return _git_bytes(source, *args).decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ExperimentError("git metadata is not UTF-8") from exc


def _git_environment() -> dict[str, str]:
    """Keep inherited Git overrides from substituting a different object database."""
    return {
        "PATH": os.defpath,
        "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
    }


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


def _extract_package(source: Path, destination: Path) -> None:
    """Copy raw blobs from the pinned tree; Git archive would apply local attributes."""
    listing = _git_bytes(
        source, "ls-tree", "-rlz", UPSTREAM_COMMIT, "harness2", max_bytes=_MAX_TREE_BYTES
    )
    if not listing or not listing.endswith(b"\0"):
        raise ExperimentError("pinned package tree is missing or incomplete")
    records = listing[:-1].split(b"\0")
    if len(records) > _MAX_FILES:
        raise ExperimentError("pinned package has too many files")
    total = 0
    seen: set[PurePosixPath] = set()
    for record in records:
        try:
            metadata, raw_name = record.split(b"\t", 1)
            mode, kind, raw_oid, raw_size = metadata.split()
            name = raw_name.decode("utf-8")
            path = PurePosixPath(name)
            size = int(raw_size)
            oid = raw_oid.decode("ascii")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ExperimentError("invalid pinned tree entry") from exc
        if (
            mode not in (b"100644", b"100755")
            or kind != b"blob"
            or not re.fullmatch(r"[0-9a-f]{40}", oid)
            or size < 0
            or size > _MAX_FILE_BYTES
            or path.is_absolute()
            or ".." in path.parts
            or len(path.parts) < 2
            or path.parts[0] != "harness2"
            or path.as_posix() != name
            or path in seen
        ):
            raise ExperimentError("unexpected pinned tree entry")
        total += size
        if total > _MAX_PACKAGE_BYTES:
            raise ExperimentError("pinned package exceeds the experiment limit")
        seen.add(path)
        content = _git_bytes(source, "cat-file", "blob", oid, max_bytes=size)
        if len(content) != size:
            raise ExperimentError("pinned blob size does not match its tree entry")
        target = destination.joinpath(*path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _sandbox_command(
    rootfs: Path, package_root: Path, output_root: Path, python: Path, bwrap: str
) -> list[str]:
    """Expose only a read-only Python runtime, package, and disposable output."""
    return [
        bwrap, "--unshare-all", "--unshare-user", "--disable-userns",
        "--die-with-parent", "--new-session", "--clearenv",
        "--ro-bind", str(rootfs), "/",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", str(package_root), "/source",
        "--bind", str(output_root), "/output",
        "--chdir", "/output",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "HOME", "/output/home",
        "--setenv", "XDG_CONFIG_HOME", "/output/home/.config",
        "--setenv", "XDG_CACHE_HOME", "/output/home/.cache",
        "--setenv", "TMPDIR", "/output/tmp",
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--", str(python), "-I", "-B", "-c", _DEMO_CODE, "/source",
        "--output", "/output/demo", "--k", "1", "--mode", "both",
    ]


def _prepare_rootfs(rootfs: Path) -> None:
    for directory in ("usr", "source", "output", "tmp"):
        (rootfs / directory).mkdir(parents=True)
    (rootfs / "lib").symlink_to("usr/lib", target_is_directory=True)
    (rootfs / "lib64").symlink_to("usr/lib64", target_is_directory=True)


def _run_sandboxed_demo(rootfs: Path, package_root: Path, output_root: Path) -> None:
    bwrap = shutil.which("bwrap", path=os.defpath)
    if bwrap is None:
        raise ExperimentError("bubblewrap sandbox is unavailable")
    if sys.version_info[:2] not in ((3, 12), (3, 13)):
        raise ExperimentError("Harness² demo requires Python 3.12 or 3.13")
    python = Path(sys.executable).resolve()
    if not python.is_file() or not python.is_relative_to(Path("/usr")):
        raise ExperimentError("Python interpreter is outside the sandbox runtime")
    command = _sandbox_command(rootfs, package_root, output_root, python, bwrap)
    with (
        (output_root / "stdout.log").open("xb+") as stdout,
        (output_root / "stderr.log").open("xb+") as stderr,
    ):
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                env={"PATH": os.defpath},
                start_new_session=True,
            )
        except OSError as exc:
            raise ExperimentError(f"cannot start sandbox: {exc}") from exc
        try:
            returncode = process.wait(timeout=120)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise ExperimentError("sandboxed demo timed out") from exc
        if returncode:
            stderr.seek(0)
            detail = stderr.read(1000).decode(errors="replace").strip()
            raise ExperimentError(f"sandboxed demo failed ({returncode}): {detail}")


def _read_demo_report(output_root: Path) -> dict[str, int]:
    """Read a bounded regular report without following guest-created links."""
    try:
        root_fd = os.open(output_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            demo_fd = os.open("demo", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=root_fd)
            try:
                report_fd = os.open("demo.json", os.O_RDONLY | os.O_NOFOLLOW,
                                    dir_fd=demo_fd)
                with os.fdopen(report_fd, "rb") as report_file:
                    info = os.fstat(report_file.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_REPORT_BYTES:
                        raise ExperimentError("demo report is not a bounded regular file")
                    data = report_file.read(_MAX_REPORT_BYTES + 1)
            finally:
                os.close(demo_fd)
        finally:
            os.close(root_fd)
    except OSError as exc:
        raise ExperimentError(f"demo report unavailable: {exc}") from exc
    if len(data) > _MAX_REPORT_BYTES:
        raise ExperimentError("demo report exceeds the experiment limit")
    try:
        report = json.loads(data)
    except ValueError as exc:
        raise ExperimentError(f"demo report is invalid JSON: {exc}") from exc
    if not isinstance(report, dict):
        raise ExperimentError("demo report is not an object")
    fields = ("done", "failed", "execution_count", "editor_calls")
    if any(type(report.get(field)) is not int or not 0 <= report[field] <= 100_000
           for field in fields):
        raise ExperimentError("demo report has invalid counters")
    if report["done"] != 2 or report["failed"] != 0:
        raise ExperimentError("demo did not complete both tasks")
    return {field: report[field] for field in fields}


def run_demo(source: Path) -> dict[str, int | str]:
    """Exercise both deterministic recursion modes in a disposable directory."""
    source = verify_source(source)
    with tempfile.TemporaryDirectory(prefix="geniusnew-harness2-") as temporary:
        root = Path(temporary)
        package_root = root / "source"
        package_root.mkdir()
        _extract_package(source, package_root)
        output = root / "output"
        output.mkdir()
        (output / "home").mkdir()
        (output / "tmp").mkdir()
        rootfs = root / "rootfs"
        _prepare_rootfs(rootfs)
        _run_sandboxed_demo(rootfs, package_root, output)
        return {"upstream_commit": UPSTREAM_COMMIT,
                **_read_demo_report(output)}


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
