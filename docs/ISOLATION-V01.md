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
  `os.posix_spawn`, `os.system`, `subprocess.Popen`, `os.kill`); `_posixsubprocess`
  raises none and is not covered (see Known gaps);
- refuses `ctypes` audit operations;
- refuses writes opened outside the temporary directory and low-level write opens whose
  `dir_fd` cannot be proven safe;
- refuses filesystem mutation APIs such as rename, remove, link, symlink, chmod, and
  truncate from worker code.

A denied operation becomes a signed `FAILED / ISOLATION_VIOLATED` result. A wall-clock
or process-resource termination becomes `FAILED / RESOURCE_EXHAUSTED`. Worker
exceptions become `FAILED / WORKER_FAILED`; their exception text never crosses the
process boundary.

## Tests are the claim

`tests/test_isolation.py` verifies that:

- the deterministic reference worker returns the same signed result in and out of the
  process boundary;
- no `WorkerAuthority` instance exists in the fresh worker interpreter;
- socket creation is denied;
- a write outside the sandbox cannot create its target;
- a write inside the sandbox is allowed and the directory is deleted before return;
- a child cannot fork another process through the Python API;
- the child cannot read the parent environment through `/proc` or replace its resource
  limits;
- an overlong worker is killed by the parent deadline;
- configured POSIX resource limits are visible inside the child;
- exception text does not leak;
- malformed output still passes through the parent-side result contract.

`geniusnew/isolation.py` is also included in `scripts/refusals.py`, so deleting any
security refusal must make the CI suite fail.

## Known gaps (held open)

Two gaps are measured rather than assumed. Each has a test that asserts the gap
exists, so closing it without updating `SECURITY.md` turns the suite red:

- **Spawning below the hook.** The sandbox is a Python audit hook, and
  `_posixsubprocess.fork_exec` raises no audit event. Worker code reaching it, for
  example through `multiprocessing.util.spawnv_passfds`, starts a process the hook never
  sees; that process writes outside the temporary directory and is bound only by the
  inherited resource limits.
  `test_a_spawn_below_the_audit_hook_escapes_and_this_is_the_boundary`.
- **Reading the host.** Reads outside `/proc`, `/sys` and `/dev` are allowed, and a
  worker's output goes back to the client.
  `test_a_read_outside_the_temporary_directory_is_allowed_and_this_is_the_boundary`.

Both need worker code to be hostile; a client chooses a payload, never the worker.
Closing them takes an OS-enforced sandbox (seccomp, Landlock, namespaces), not another
hook rule: a module already imported by the child raises no import event either.

## Deliberate non-goals

This is process-level isolation for the Python v0.1 worker path. It is not a microVM,
container security boundary, seccomp profile, or proof against hostile native code,
preloaded FFI objects, kernel exploits, or direct raw syscalls. The roadmap explicitly
keeps Firecracker/microVM isolation outside v0.1.

If GeniusNew later executes arbitrary native extensions or adversarial third-party code,
this boundary must be replaced or wrapped by an OS-enforced sandbox rather than described
as stronger than it is.
