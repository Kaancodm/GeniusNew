# Process Isolation v0.1

This document states exactly what GeniusNew's step-11 worker boundary enforces.

## Trust split

`IsolatedWorkerRunner` keeps the `WorkerAuthority` and result signing key in the
parent process. The worker is launched through **exec into a fresh Python interpreter**;
there is no forked copy of the parent's address space. Only an importable worker class
identifier, canonical-JSON instance state, limits and a copy of the payload cross that
boundary. The child returns an untrusted, bounded canonical-JSON message; the parent
applies the normal result contract before signing anything.

## Enforced boundary

For the v0.1 Python worker path the child process:

- starts in a fresh interpreter with inherited file descriptors closed, stdin isolated,
  and stdout/stderr detached from worker-controlled protocol output;
- starts in a fresh per-job temporary directory;
- has a wall-clock deadline enforced by the parent;
- receives POSIX limits for CPU time, address space, file size, open file descriptors,
  and core dumps;
- has its environment replaced with values rooted in the temporary directory;
- refuses Python socket operations;
- refuses worker reads through `/proc`, `/sys`, and `/dev`, including the parent
  process environment/FD view; every other path the service user can read stays
  readable (see Known gaps);
- refuses process creation, exec, shell launch, and signals aimed at other processes
  through the Python APIs that raise audit events (`os.fork`, `os.exec*`, `os.spawn*`,
  `os.posix_spawn`, `os.system`, `subprocess.Popen`, `os.kill`);
- is killed by the kernel when it tries to start a process or program at all, whether
  or not Python raises an audit event (`_posixsubprocess` raises none). A seccomp
  filter, installed before the audit hook and irremovable afterwards, kills the child
  on `fork`, `vfork`, `execve`, `execveat`, `clone` without `CLONE_THREAD`, and any
  syscall from a foreign ABI (x32, or i386 on x86_64). `clone3` gets `ENOSYS`, because
  its flags are in memory the filter cannot read; libc then falls back to `clone`.
  Threads stay allowed;
- refuses `ctypes` audit operations;
- refuses writes opened outside the temporary directory and low-level write opens whose
  `dir_fd` cannot be proven safe;
- refuses filesystem mutation APIs such as rename, remove, link, symlink, chmod, and
  truncate from worker code.

A denied operation becomes a signed `FAILED / ISOLATION_VIOLATED` result, and so does a
child the filter killed: only seccomp sends `SIGSYS`. A wall-clock or other
process-resource termination becomes `FAILED / RESOURCE_EXHAUSTED`. Worker
exceptions become `FAILED / WORKER_FAILED`; their exception text never crosses the
process boundary.

The filter exists for Linux on x86_64 and aarch64 with a 64-bit interpreter. Anywhere
else, including Windows and macOS, `IsolatedWorkerRunner` refuses to start (fail
closed), just as it does without POSIX resource limits.

## Tests are the claim

`tests/test_isolation.py` verifies that:

- the deterministic reference worker returns the same signed result in and out of the
  process boundary;
- no `WorkerAuthority` instance exists in the fresh worker interpreter;
- socket creation is denied;
- a write outside the sandbox cannot create its target;
- a write inside the sandbox is allowed and the directory is deleted before return;
- a child cannot fork another process through the Python API;
- a child that starts a shell through `_posixsubprocess` is killed before the shell can
  write anything, and the result is `ISOLATION_VIOLATED`;
- a worker can still start a thread;
- each filter rule holds on its own: a raw `fork`, `vfork`, `execve`, `execveat`, `clone`
  without `CLONE_THREAD`, x32 syscall and foreign-ABI syscall is killed, `clone3` gets
  `ENOSYS`, and `getpid` passes. The test names these syscalls itself instead of
  reading the filter's table, so an entry missing from the table is noticed;
- the child cannot read the parent environment through `/proc` or replace its resource
  limits;
- an overlong worker is killed by the parent deadline;
- configured POSIX resource limits are visible inside the child;
- exception text does not leak;
- malformed output still passes through the parent-side result contract.

`geniusnew/isolation.py` is also included in `scripts/refusals.py`, so deleting any
security refusal must make the CI suite fail.

## Known gaps (held open)

One gap is measured rather than assumed. Its test asserts that the gap exists, so
closing it without updating `SECURITY.md` turns the suite red:

- **Reading the host.** Reads outside `/proc`, `/sys` and `/dev` are allowed, and a
  worker's output goes back to the client.
  `test_a_read_outside_the_temporary_directory_is_allowed_and_this_is_the_boundary`.

It takes worker code that is hostile: a client chooses a payload, never the worker.
Closing it takes a kernel-enforced path allowlist (Landlock), not another hook rule: a
module the child has already imported raises no import event either.

Spawning below the hook was the second held-open gap. The seccomp filter closes it,
and `test_a_spawn_below_the_audit_hook_is_killed_by_the_kernel` now asserts the
refusal.

## Deliberate non-goals

This is process-level isolation for the Python v0.1 worker path. It is not a microVM,
container security boundary, or proof against hostile native code, preloaded FFI objects
or kernel exploits. The seccomp filter covers starting processes and programs only.
Raw network and file syscalls are still checked by nothing but the audit hook. The roadmap explicitly
keeps Firecracker/microVM isolation outside v0.1.

If GeniusNew later executes arbitrary native extensions or adversarial third-party code,
this boundary must be replaced or wrapped by an OS-enforced sandbox rather than described
as stronger than it is.
