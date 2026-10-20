"""Run the pinned Harness² local demo without installing it into GeniusNew."""

from __future__ import annotations

import argparse
from functools import partial
import hashlib
import json
import os
import re
import resource
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
_MAX_TREES = 128
_MAX_REPORT_BYTES = 64 * 1024
_MAX_LOG_BYTES = 128 * 1024
_OUTPUT_TMPFS_BYTES = 16 * 1024 * 1024
_MAX_ADDRESS_SPACE_BYTES = 256 * 1024 * 1024
_MAX_PROCESSES = 16
_DEMO_CODE = (
    "import sys; sys.path.insert(0, '/source'); "
    "from harness2.cli.demo import main; "
    "raise SystemExit(main(['--output', '/output/demo', '--k', '1', '--mode', 'both']))"
)


class ExperimentError(Exception):
    """The source proof or the temporary demo run failed."""


def _git_bytes(source: Path, *args: str, max_bytes: int = 16 * 1024) -> bytes:
    try:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(
                ["git", "--no-replace-objects", "-C", str(source), *args],
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                env=_git_environment(),
                start_new_session=True,
                preexec_fn=partial(_limit_git_file_size, max(max_bytes, 16 * 1024)),
            )
            try:
                returncode = process.wait(timeout=15)
            except subprocess.TimeoutExpired as exc:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                raise ExperimentError("git command timed out") from exc
            stdout.seek(0)
            data = stdout.read(max_bytes + 1)
            stderr.seek(0)
            error = stderr.read(16 * 1024)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExperimentError(f"git unavailable: {exc}") from exc
    if len(data) > max_bytes:
        raise ExperimentError("git object exceeds the experiment limit")
    if returncode:
        detail = _safe_detail(error)
        raise ExperimentError(f"git {' '.join(args)} failed: {detail}")
    return data


def _limit_git_file_size(max_bytes: int) -> None:
    limits = (
        (resource.RLIMIT_FSIZE, max_bytes + 1),
        (resource.RLIMIT_AS, _MAX_ADDRESS_SPACE_BYTES),
        (resource.RLIMIT_NOFILE, 64),
        (resource.RLIMIT_CPU, 5),
        (resource.RLIMIT_CORE, 0),
    )
    for kind, target in limits:
        soft, hard = resource.getrlimit(kind)
        bound = min(value for value in (target, soft, hard)
                    if value != resource.RLIM_INFINITY)
        resource.setrlimit(kind, (bound, bound))


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


def _safe_detail(raw: bytes, limit: int = 500) -> str:
    return "".join(char if 32 <= ord(char) < 127 else "?"
                   for char in raw[-limit:].decode(errors="replace")).strip()


def verify_source(source: Path) -> Path:
    """Require the official remote and exact pinned checkout before reading objects."""
    try:
        source = source.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ExperimentError(f"source unavailable: {exc}") from exc
    if Path(_git(source, "rev-parse", "--show-toplevel")).resolve() != source:
        raise ExperimentError("source must be the checkout root")
    if _git(source, "remote", "get-url", "origin") != UPSTREAM_URL:
        raise ExperimentError("source origin is not the official Harness² repository")
    if _git(source, "rev-parse", "HEAD") != UPSTREAM_COMMIT:
        raise ExperimentError(f"source HEAD must be {UPSTREAM_COMMIT}")
    _extract_package(source, None)
    return source


def _read_object(source: Path, kind: str, oid: str, max_bytes: int) -> bytes:
    content = _git_bytes(source, "cat-file", kind, oid, max_bytes=max_bytes)
    canonical = kind.encode("ascii") + b" " + str(len(content)).encode("ascii") + b"\0" + content
    if hashlib.sha1(canonical).hexdigest() != oid:
        raise ExperimentError(f"pinned {kind} hash does not match its object ID")
    return content


def _tree_entries(content: bytes) -> list[tuple[bytes, str, str]]:
    """Decode raw Git tree records rather than Git's filtered worktree view."""
    entries = []
    seen: set[str] = set()
    offset = 0
    while offset < len(content):
        space = content.find(b" ", offset)
        end = content.find(b"\0", space + 1) if space >= 0 else -1
        if space <= offset or end <= space + 1 or end + 21 > len(content):
            raise ExperimentError("invalid pinned tree entry")
        mode = content[offset:space]
        raw_name = content[space + 1:end]
        oid = content[end + 1:end + 21].hex()
        offset = end + 21
        try:
            name = raw_name.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ExperimentError("invalid pinned tree entry") from exc
        if not name or name in (".", "..") or "/" in name or name in seen:
            raise ExperimentError("invalid pinned tree entry")
        seen.add(name)
        entries.append((mode, name, oid))
    return entries


def _extract_package(source: Path, destination: Path | None) -> None:
    """Verify the exact commit/tree chain and copy only hashed package blobs."""
    commit = _read_object(source, "commit", UPSTREAM_COMMIT, 16 * 1024)
    first_line = commit.split(b"\n", 1)[0]
    if not re.fullmatch(rb"tree [0-9a-f]{40}", first_line):
        raise ExperimentError("pinned commit has no valid root tree")
    root = _read_object(source, "tree", first_line[5:].decode("ascii"), _MAX_TREE_BYTES)
    package = [(mode, oid) for mode, name, oid in _tree_entries(root) if name == "harness2"]
    if len(package) != 1 or package[0][0] != b"40000":
        raise ExperimentError("pinned package tree is missing")
    stack = [(PurePosixPath("harness2"), package[0][1])]
    tree_bytes = len(root)
    tree_count = 0
    file_count = 0
    total = 0
    while stack:
        directory, tree_oid = stack.pop()
        tree_count += 1
        if tree_count > _MAX_TREES:
            raise ExperimentError("pinned package has too many trees")
        tree = _read_object(source, "tree", tree_oid, _MAX_TREE_BYTES - tree_bytes)
        tree_bytes += len(tree)
        for mode, name, oid in _tree_entries(tree):
            path = directory / name
            if mode == b"40000":
                stack.append((path, oid))
                continue
            if mode not in (b"100644", b"100755"):
                raise ExperimentError("unexpected pinned tree entry")
            file_count += 1
            if file_count > _MAX_FILES:
                raise ExperimentError("pinned package has too many files")
            content = _read_object(source, "blob", oid, _MAX_FILE_BYTES)
            total += len(content)
            if total > _MAX_PACKAGE_BYTES:
                raise ExperimentError("pinned package exceeds the experiment limit")
            if destination is not None:
                target = destination.joinpath(*path.parts)
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                except OSError as exc:
                    raise ExperimentError(f"cannot extract pinned package: {exc}") from exc
    if not file_count:
        raise ExperimentError("pinned package tree is empty")


def _sandbox_command(rootfs: Path, package_root: Path, python: Path, bwrap: str) -> list[str]:
    """Bound guest writes to private tmpfs and the capped log descriptors."""
    return [
        bwrap, "--unshare-all", "--unshare-user", "--disable-userns",
        "--uid", "65534", "--gid", "65534",
        "--die-with-parent", "--new-session", "--clearenv",
        "--ro-bind", str(rootfs), "/",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", str(package_root), "/source",
        "--ro-bind", str(Path(__file__).resolve()), "/runner.py",
        "--size", str(_OUTPUT_TMPFS_BYTES), "--tmpfs", "/output",
        "--dir", "/output/home",
        "--dir", "/output/tmp",
        "--chdir", "/output",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "HOME", "/output/home",
        "--setenv", "XDG_CONFIG_HOME", "/output/home/.config",
        "--setenv", "XDG_CACHE_HOME", "/output/home/.cache",
        "--setenv", "TMPDIR", "/output/tmp",
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--", str(python), "-I", "-B", "/runner.py", "--guest-demo",
    ]


def _prepare_rootfs(rootfs: Path) -> None:
    for directory in ("usr", "source", "output", "tmp"):
        (rootfs / directory).mkdir(parents=True)
    (rootfs / "lib").symlink_to("usr/lib", target_is_directory=True)
    (rootfs / "lib64").symlink_to("usr/lib64", target_is_directory=True)
    (rootfs / "runner.py").touch()


def _limit_guest_resources() -> None:
    """Limit inherited descriptors and processes before executing bubblewrap."""
    limits = (
        (resource.RLIMIT_FSIZE, _MAX_LOG_BYTES),
        (resource.RLIMIT_AS, _MAX_ADDRESS_SPACE_BYTES),
        (resource.RLIMIT_NOFILE, 64),
        (resource.RLIMIT_CPU, 60),
        (resource.RLIMIT_CORE, 0),
    )
    for kind, target in limits:
        soft, hard = resource.getrlimit(kind)
        bound = min(value for value in (target, soft, hard)
                    if value != resource.RLIM_INFINITY)
        resource.setrlimit(kind, (bound, bound))


def _guest_demo() -> int:
    """Run only inside the bwrap guest and return a validated small report."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NPROC)
    bound = min(value for value in (_MAX_PROCESSES, soft, hard)
                if value != resource.RLIM_INFINITY)
    resource.setrlimit(resource.RLIMIT_NPROC, (bound, bound))
    with (Path("/output/upstream.log")).open("xb+") as log:
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", _DEMO_CODE],
            input=b"",
            stdout=log,
            stderr=log,
            timeout=90,
            check=False,
            close_fds=True,
        )
        if result.returncode:
            log.seek(0)
            raise ExperimentError(
                f"upstream demo failed ({result.returncode}): {_safe_detail(log.read(1000), 1000)}"
            )
    report = _read_demo_report(Path("/output"))
    print(json.dumps(report, sort_keys=True))
    return 0


def _run_sandboxed_demo(
    rootfs: Path, package_root: Path, output_root: Path
) -> dict[str, int]:
    bwrap = shutil.which("bwrap", path=os.defpath)
    if bwrap is None:
        raise ExperimentError("bubblewrap sandbox is unavailable")
    if sys.version_info[:2] not in ((3, 12), (3, 13)):
        raise ExperimentError("Harness² demo requires Python 3.12 or 3.13")
    python = Path(sys.executable).resolve()
    if not python.is_file() or not python.is_relative_to(Path("/usr")):
        raise ExperimentError("Python interpreter is outside the sandbox runtime")
    command = _sandbox_command(rootfs, package_root, python, bwrap)
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
                preexec_fn=_limit_guest_resources,
            )
        except (OSError, subprocess.SubprocessError) as exc:
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
            detail = _safe_detail(stderr.read(1000), 1000)
            raise ExperimentError(f"sandboxed demo failed ({returncode}): {detail}")
        stdout.seek(0)
        return _parse_demo_report(stdout.read(_MAX_REPORT_BYTES + 1))


def _read_demo_report(output_root: Path) -> dict[str, int]:
    """Read a bounded regular report without following guest-created links."""
    try:
        root_fd = os.open(output_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            demo_fd = os.open("demo", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=root_fd)
            try:
                report_fd = os.open("demo.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
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
    return _parse_demo_report(data)


def _parse_demo_report(data: bytes) -> dict[str, int]:
    if len(data) > _MAX_REPORT_BYTES:
        raise ExperimentError("demo report exceeds the experiment limit")
    try:
        report = json.loads(data)
    except (ValueError, UnicodeError, RecursionError) as exc:
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
        rootfs = root / "rootfs"
        _prepare_rootfs(rootfs)
        report = _run_sandboxed_demo(rootfs, package_root, output)
        return {"upstream_commit": UPSTREAM_COMMIT, **report}


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
    if sys.argv[1:] == ["--guest-demo"]:
        raise SystemExit(_guest_demo())
    raise SystemExit(main())
