import errno
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from geniusnew.approvals import ApprovalStore
from geniusnew.contracts import ContractError, Grant, HandoffSigner, Policy, canonical, issue, validate
from geniusnew.gateway import Gateway
import geniusnew.isolation as isolation_module
import geniusnew.isolation_child as isolation_child_module
from geniusnew.isolation import IsolationLimits, IsolatedWorkerRunner
from geniusnew.results import WorkerAuthority, accept
from geniusnew.workers import (
    DeterministicSummarizer,
    Worker,
    WorkerRunner,
    _WorkerIsolationViolation,
    _WorkerResourceExhausted,
)

# The runner refuses to start without both; elsewhere only its refusal is tested.
_ISOLATION_SUPPORTED = (isolation_module._resource_supported()
                        and isolation_module._process_filter_supported())


class ReturningWorker(Worker):
    tool = "summarize"

    def __init__(self, value):
        self.value = value

    def run(self, payload):
        return self.value


class NetworkWorker(Worker):
    tool = "summarize"

    def run(self, payload):
        import socket

        with socket.socket() as client:
            client.connect(("127.0.0.1", 9))
        return {"text": "network unexpectedly worked"}


class OutsideWriteWorker(Worker):
    tool = "summarize"

    def __init__(self, path):
        self.path = path

    def run(self, payload):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("escape")
        return {"text": "outside write unexpectedly worked"}


class InsideWriteWorker(Worker):
    tool = "summarize"

    def run(self, payload):
        with open("inside.txt", "w", encoding="utf-8") as handle:
            handle.write(payload["text"])
        return {"text": os.getcwd()}


class SpawnWorker(Worker):
    tool = "summarize"

    def run(self, payload):
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)
        return {"text": "spawn unexpectedly worked"}


class NativeSpawnWorker(Worker):
    """Starts a shell through the one spawn path that raises no audit event.

    `spawnv_passfds` is the stdlib's own thin wrapper around
    `_posixsubprocess.fork_exec`, kept in step with it on every Python version,
    so the test does not have to track that function's changing signature.
    """

    tool = "summarize"

    def __init__(self, path):
        self.path = path

    def run(self, payload):
        from multiprocessing.util import spawnv_passfds

        # The path travels as $0, never through the shell's parser.
        spawnv_passfds(b"/bin/sh", [b"/bin/sh", b"-c", b'echo escaped > "$0"',
                                    os.fsencode(self.path)], ())
        return {"text": "spawned"}


class ThreadWorker(Worker):
    """Starts a thread: the filter must tell a thread from a new process."""

    tool = "summarize"

    def run(self, payload):
        import threading

        seen = []
        thread = threading.Thread(target=seen.append, args=(payload["text"],))
        thread.start()
        thread.join()
        return {"text": seen[0]}


class OutsideReadWorker(Worker):
    tool = "summarize"

    def __init__(self, path):
        self.path = path

    def run(self, payload):
        with open(self.path, encoding="utf-8") as handle:
            return {"text": handle.read()}


class ParentProcReadWorker(Worker):
    tool = "summarize"

    def run(self, payload):
        with open(f"/proc/{os.getppid()}/environ", "rb") as handle:
            data = handle.read()
        return {"text": str(len(data))}


class CtypesWorker(Worker):
    tool = "summarize"

    def run(self, payload):
        import ctypes

        ctypes.CDLL(None)
        return {"text": "ctypes unexpectedly worked"}


class FilesystemMutationWorker(Worker):
    tool = "summarize"

    def run(self, payload):
        os.mkdir("nested")
        return {"text": "mkdir unexpectedly worked"}


class LimitTamperWorker(Worker):
    tool = "summarize"

    def run(self, payload):
        import resource

        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        return {"text": "limit tamper unexpectedly worked"}


class SlowWorker(Worker):
    tool = "summarize"

    def run(self, payload):
        time.sleep(5)
        return {"text": "late"}


class LimitsWorker(Worker):
    tool = "summarize"

    def run(self, payload):
        import resource

        cpu = resource.getrlimit(resource.RLIMIT_CPU)[0]
        memory = resource.getrlimit(resource.RLIMIT_AS)[0]
        file_size = resource.getrlimit(resource.RLIMIT_FSIZE)[0]
        open_files = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        return {"text": f"cpu={cpu};memory={memory};file={file_size};open={open_files}"}


class ExplodingWorker(Worker):
    tool = "summarize"

    def run(self, payload):
        raise RuntimeError("EXCEPTION-TEXT-MUST-STAY-IN-THE-CHILD")


class AuthorityProbeWorker(Worker):
    tool = "summarize"

    def run(self, payload):
        import gc
        from geniusnew.results import WorkerAuthority

        present = any(isinstance(value, WorkerAuthority) for value in gc.get_objects())
        return {"text": "authority-present" if present else "authority-absent"}


class CallerFrameProbeWorker(Worker):
    tool = "summarize"

    def run(self, payload):
        import inspect
        from geniusnew.results import WorkerAuthority
        from geniusnew.workers import WorkerRunner

        frame = inspect.currentframe()
        present = False
        try:
            while frame is not None:
                for value in frame.f_locals.values():
                    if isinstance(value, (WorkerAuthority, WorkerRunner)):
                        present = True
                        break
                if present:
                    break
                frame = frame.f_back
        finally:
            del frame
        return {"text": "caller-authority-present" if present
                else "caller-authority-absent"}


class IsolationLimitsTest(unittest.TestCase):
    def test_limits_reject_values_outside_the_v01_envelope(self):
        cases = [
            {"wall_seconds": 0},
            {"wall_seconds": 31},
            {"wall_seconds": True},
            {"cpu_seconds": 0},
            {"cpu_seconds": 11},
            {"cpu_seconds": True},
            {"memory_bytes": 64 * 1024 * 1024},
            {"memory_bytes": 3 * 1024 * 1024 * 1024},
            {"max_file_bytes": 1024},
            {"max_file_bytes": 32 * 1024 * 1024},
            {"max_open_files": 8},
            {"max_open_files": 512},
        ]
        for over in cases:
            with self.subTest(over=over), self.assertRaises(ContractError):
                IsolationLimits(**over)

    def test_the_documented_boundary_values_are_accepted(self):
        self.assertEqual(IsolationLimits(wall_seconds=0.05).wall_seconds, 0.05)
        self.assertEqual(IsolationLimits(wall_seconds=30).wall_seconds, 30)
        self.assertEqual(IsolationLimits(cpu_seconds=10).cpu_seconds, 10)
        self.assertEqual(
            IsolationLimits(memory_bytes=128 * 1024 * 1024).memory_bytes,
            128 * 1024 * 1024,
        )
        self.assertEqual(IsolationLimits(max_file_bytes=4096).max_file_bytes, 4096)
        self.assertEqual(IsolationLimits(max_open_files=16).max_open_files, 16)

    @unittest.skipUnless(_ISOLATION_SUPPORTED, "POSIX limits and a seccomp filter required")
    def test_runner_requires_a_limits_object(self):
        authority = WorkerAuthority(result_key=b"a-separate-result-key-of-32bytes!")
        with self.assertRaisesRegex(ContractError, "IsolationLimits"):
            IsolatedWorkerRunner(
                DeterministicSummarizer(), authority=authority, limits="not-limits"
            )

    def test_runner_fails_closed_without_resource_limits(self):
        authority = WorkerAuthority(result_key=b"a-separate-result-key-of-32bytes!")
        with patch.object(isolation_module, "_resource_supported", return_value=False):
            with self.assertRaisesRegex(ContractError, "POSIX resource limits"):
                IsolatedWorkerRunner(DeterministicSummarizer(), authority=authority)

    def test_runner_fails_closed_without_a_process_filter(self):
        authority = WorkerAuthority(result_key=b"a-separate-result-key-of-32bytes!")
        with patch.object(isolation_module, "_resource_supported", return_value=True), \
                patch.object(isolation_module, "_process_filter_supported", return_value=False):
            with self.assertRaisesRegex(ContractError, "seccomp process filter"):
                IsolatedWorkerRunner(DeterministicSummarizer(), authority=authority)

    def test_local_worker_classes_are_not_accepted_for_exec_isolation(self):
        class LocalWorker(Worker):
            tool = "summarize"

            def run(self, payload):
                return {"text": "no"}

        with self.assertRaisesRegex(ContractError, "importable"):
            isolation_module._worker_spec(LocalWorker())

    def test_worker_state_keys_must_be_strings(self):
        worker = DeterministicSummarizer()
        worker.__dict__[1] = "bad-key"
        with self.assertRaisesRegex(ContractError, "state keys"):
            isolation_module._worker_spec(worker)

    def test_the_internal_request_is_bounded_before_starting_a_child(self):
        worker = ReturningWorker({"text": "x" * 40000})
        with self.assertRaisesRegex(ContractError, "request is too large"):
            isolation_module._request(
                worker, {"text": "payload"}, IsolationLimits()
            )


class IsolationChildContractTest(unittest.TestCase):
    def request_value(self, **over):
        value = {
            "version": 1,
            "worker": {
                "module": "geniusnew.workers",
                "qualname": "DeterministicSummarizer",
                "state": {},
                "tool": "summarize",
            },
            "payload": {"text": "hello"},
            "limits": {
                "wall_seconds": 2.0,
                "cpu_seconds": 1,
                "memory_bytes": 512 * 1024 * 1024,
                "max_file_bytes": 1024 * 1024,
                "max_open_files": 64,
            },
        }
        value.update(over)
        return value

    def test_request_size_guard_rejects_an_otherwise_valid_oversized_request(self):
        value = self.request_value(payload={"text": "x" * 40000})
        wire = canonical(value)
        self.assertGreater(len(wire), isolation_module._MAX_CHILD_REQUEST_BYTES)
        with self.assertRaisesRegex(ContractError, "invalid isolation request"):
            isolation_child_module._decode_request(wire)

    def test_noncanonical_request_is_rejected_by_its_own_check(self):
        import json

        value = self.request_value()
        wire = json.dumps(value, indent=1, sort_keys=True).encode("ascii")
        self.assertNotEqual(wire, canonical(value))
        with self.assertRaisesRegex(ContractError, "invalid isolation request"):
            isolation_child_module._decode_request(wire)

    def test_request_version_and_exact_top_level_keys_are_enforced(self):
        with self.assertRaisesRegex(ContractError, "invalid isolation request"):
            isolation_child_module._decode_request(
                canonical(self.request_value(version=2))
            )
        extra = self.request_value()
        extra["extra"] = "no"
        with self.assertRaisesRegex(ContractError, "invalid isolation request"):
            isolation_child_module._decode_request(canonical(extra))

    def test_worker_container_must_actually_be_a_dict(self):
        worker_as_list = ["module", "qualname", "state", "tool"]
        with self.assertRaisesRegex(ContractError, "invalid isolation worker"):
            isolation_child_module._decode_request(
                canonical(self.request_value(worker=worker_as_list))
            )

    def test_worker_keys_are_exact(self):
        worker = {
            "module": "geniusnew.workers",
            "qualname": "DeterministicSummarizer",
            "tool": "summarize",
        }
        with self.assertRaisesRegex(ContractError, "invalid isolation worker"):
            isolation_child_module._decode_request(
                canonical(self.request_value(worker=worker))
            )

    def good_spec(self, **over):
        spec = {
            "module": "geniusnew.workers",
            "qualname": "DeterministicSummarizer",
            "state": {},
            "tool": "summarize",
        }
        spec.update(over)
        return spec

    def test_worker_identity_fields_must_be_nonempty_strings(self):
        with self.assertRaisesRegex(ContractError, "invalid isolation worker"):
            isolation_child_module._resolve_worker(self.good_spec(module=42))

    def test_worker_state_must_be_a_string_keyed_dict(self):
        with self.assertRaisesRegex(ContractError, "invalid isolation worker"):
            isolation_child_module._resolve_worker(self.good_spec(state={1: "bad"}))

    def test_resolved_target_must_be_a_worker_class(self):
        with self.assertRaisesRegex(ContractError, "invalid isolation worker"):
            isolation_child_module._resolve_worker(
                self.good_spec(module="builtins", qualname="str")
            )

    def test_tool_identity_cannot_change_across_exec(self):
        with self.assertRaisesRegex(ContractError, "tool changed"):
            isolation_child_module._resolve_worker(self.good_spec(tool="other"))


# Written out again rather than read from `_FILTER_ARCHES`: a test that iterates
# the table it checks cannot notice an entry missing from it. `getpid` is the
# control that shows a probe can pass at all.
_SYSCALLS = {
    "x86_64": {"getpid": 39, "clone": 56, "fork": 57, "vfork": 58, "execve": 59,
               "execveat": 322, "clone3": 435},
    "aarch64": {"getpid": 172, "clone": 220, "execve": 221, "execveat": 281,
                "clone3": 435},
}
_PROBE = r"""
import ctypes, os, sys
import geniusnew.isolation as isolation

machine = os.uname().machine
if sys.argv[1] == "foreign-arch":
    isolation._FILTER_ARCHES[machine] = dict(isolation._FILTER_ARCHES[machine], arch=0)
syscall = ctypes.CDLL(None, use_errno=True).syscall
syscall.restype = ctypes.c_long
syscall.argtypes = (ctypes.c_long,) * 6
values = [int(value) for value in sys.argv[2:]]
isolation._install_process_filter()
result = syscall(*values, *[0] * (6 - len(values)))
os._exit(100 + ctypes.get_errno() if result < 0 else 0)
"""


class ProcessFilterContractTest(unittest.TestCase):
    def test_an_unknown_architecture_gets_no_filter(self):
        with self.assertRaisesRegex(ContractError, "architecture"):
            isolation_module._process_filter("sparc64")

    def test_sigsys_from_the_child_is_an_isolation_violation(self):
        if isolation_module._SIGSYS is None:
            self.skipTest("no SIGSYS on this platform")
        with self.assertRaises(_WorkerIsolationViolation):
            isolation_module._decode_child_message(b"", -signal.SIGSYS)
        # Any other signal is still a resource termination.
        with self.assertRaises(_WorkerResourceExhausted):
            isolation_module._decode_child_message(b"", -signal.SIGKILL)

    @unittest.skipUnless(isolation_module._process_filter_supported(), "Linux seccomp filter required")
    def test_install_refuses_when_no_new_privs_cannot_be_set(self):
        calls = []

        def prctl(option, *args):
            calls.append(option)
            return -1 if option == isolation_module._PR_SET_NO_NEW_PRIVS else 0

        with self.assertRaisesRegex(ContractError, "no_new_privs"):
            isolation_module._install_process_filter(prctl)
        self.assertEqual(calls, [isolation_module._PR_SET_NO_NEW_PRIVS])

    @unittest.skipUnless(isolation_module._process_filter_supported(), "Linux seccomp filter required")
    def test_install_refuses_when_the_kernel_rejects_the_filter(self):
        calls = []

        def prctl(option, *args):
            calls.append((option, args))
            return -1 if option == isolation_module._PR_SET_SECCOMP else 0

        with self.assertRaisesRegex(ContractError, "seccomp filter"):
            isolation_module._install_process_filter(prctl)
        self.assertEqual(calls[0], (isolation_module._PR_SET_NO_NEW_PRIVS, (1, 0, 0, 0)))
        option, (mode, pointer, *rest) = calls[1]
        self.assertEqual((option, mode, rest), (
            isolation_module._PR_SET_SECCOMP, isolation_module._SECCOMP_MODE_FILTER, [0, 0]))
        self.assertNotEqual(pointer, 0)


@unittest.skipUnless(isolation_module._process_filter_supported(), "Linux seccomp filter required")
class ProcessFilterTest(unittest.TestCase):
    """Each rule of the filter, driven by one raw syscall in a throwaway process.

    The worker tests reach the filter only through whatever Python and libc
    happen to call. These name each syscall the filter must stop, so a rule
    dropped from the table cannot go unnoticed.
    """

    def setUp(self):
        self.syscalls = _SYSCALLS[os.uname().machine]

    def probe(self, mode, number, *args):
        root = os.path.dirname(os.path.dirname(os.path.abspath(isolation_module.__file__)))
        completed = subprocess.run(
            [sys.executable, "-c", _PROBE, mode, str(number), *map(str, args)],
            cwd=root, capture_output=True, timeout=10,
        )
        return completed.returncode

    def test_an_ordinary_syscall_passes(self):
        self.assertEqual(self.probe("native", self.syscalls["getpid"]), 0)

    def test_every_process_or_program_start_syscall_is_killed(self):
        for name in ("fork", "vfork", "execve", "execveat"):
            if name not in self.syscalls:
                continue  # aarch64 has no fork or vfork syscall
            with self.subTest(syscall=name):
                self.assertEqual(self.probe("native", self.syscalls[name]), -signal.SIGSYS)

    def test_clone_without_clone_thread_is_killed(self):
        self.assertEqual(
            self.probe("native", self.syscalls["clone"], signal.SIGCHLD), -signal.SIGSYS)

    def test_clone3_is_refused_with_enosys_so_libc_falls_back_to_clone(self):
        self.assertEqual(self.probe("native", self.syscalls["clone3"]), 100 + errno.ENOSYS)

    def test_a_foreign_syscall_abi_is_killed(self):
        # Pretend the native ABI is foreign: then even getpid must die.
        self.assertEqual(
            self.probe("foreign-arch", self.syscalls["getpid"]), -signal.SIGSYS)

    @unittest.skipUnless(sys.platform.startswith("linux") and os.uname().machine == "x86_64",
                         "x32 exists only on x86_64")
    def test_x32_syscall_numbers_are_killed(self):
        number = 0x40000000 | self.syscalls["getpid"]
        self.assertEqual(self.probe("native", number), -signal.SIGSYS)


@unittest.skipUnless(_ISOLATION_SUPPORTED, "POSIX limits and a seccomp filter required")
class ProcessIsolationTest(unittest.TestCase):
    def setUp(self):
        self.signer = HandoffSigner(integrity_key=b"phase-2-test-integrity-key-32bytes")
        self.authority = WorkerAuthority(result_key=b"a-separate-result-key-of-32bytes!")
        grant = Grant(
            "subject-demo",
            "user-demo",
            "worker-demo",
            "basic",
            ("summarize",),
            "isolated",
            False,
        )
        self.policy = Policy(
            "policy-v1",
            "orchestrator-demo",
            60,
            ("summarize",),
            ("isolated",),
            (grant,),
        )
        wire = issue(
            {"text": "the quick brown fox"},
            subject="subject-demo",
            job_id="job-demo",
            policy=self.policy,
            signer=self.signer,
            now=100,
        )
        self.handoff = validate(
            wire,
            subject="subject-demo",
            job_id="job-demo",
            policy=self.policy,
            verifier=self.signer,
            now=101,
        )
        self.gateway = Gateway(
            gateway_id="gateway-test",
            handoff_verifier=self.signer.verifier(),
            approval_store=ApprovalStore(),
        )

    def permit_for(self, handoff=None, admitted_at=101):
        handoff = handoff or self.handoff
        return self.gateway.admit(
            handoff.to_bytes(),
            subject="subject-demo",
            job_id=handoff.job_id,
            policy=self.policy,
            now=admitted_at,
        )

    def runner(self, worker, limits=None):
        return IsolatedWorkerRunner(worker, authority=self.authority, limits=limits)

    def taken(self, wire, now=120):
        return accept(
            wire,
            handoff=self.handoff,
            verifier=self.authority,
            now=now,
        )

    def test_reference_worker_matches_the_in_process_contract(self):
        isolated = self.runner(DeterministicSummarizer()).execute(self.permit_for(self.handoff), now=110)
        direct = WorkerRunner(
            DeterministicSummarizer(), authority=self.authority
        ).execute(self.permit_for(self.handoff), now=110)
        self.assertEqual(isolated, direct)
        self.assertTrue(self.taken(isolated).succeeded)

    def test_child_pipes_are_closed_after_success_failure_and_timeout(self):
        cases = [
            (DeterministicSummarizer(), IsolationLimits(), "WORK_COMPLETED"),
            (ExplodingWorker(), IsolationLimits(), "WORKER_FAILED"),
            (SlowWorker(), IsolationLimits(wall_seconds=0.1), "RESOURCE_EXHAUSTED"),
        ]
        real_popen = subprocess.Popen
        for worker, limits, reason in cases:
            with self.subTest(reason=reason):
                processes = []

                def spawn(*args, **kwargs):
                    process = real_popen(*args, **kwargs)
                    processes.append(process)  # Retain it: GC is not cleanup.
                    return process

                try:
                    with patch.object(isolation_module.subprocess, "Popen", side_effect=spawn):
                        wire = self.runner(worker, limits).execute(self.permit_for(), now=110)
                    self.assertEqual(self.taken(wire).reason_code, reason)
                    self.assertEqual(len(processes), 1)
                    process = processes[0]
                    self.assertIsNotNone(process.poll())
                    self.assertTrue(process.stdin.closed)
                    self.assertTrue(process.stdout.closed)
                finally:
                    for process in processes:
                        isolation_module._kill_process(process)
                        process.stdin.close()
                        process.stdout.close()

    def test_reader_failure_reaps_child_and_closes_pipes(self):
        real_popen = subprocess.Popen
        processes = []

        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        try:
            with patch.object(isolation_module.subprocess, "Popen", side_effect=spawn), \
                    patch.object(isolation_module, "_read_process", side_effect=OSError("read failed")):
                wire = self.runner(SlowWorker()).execute(self.permit_for(), now=110)
            self.assertEqual(self.taken(wire).reason_code, "WORKER_FAILED")
            self.assertEqual(len(processes), 1)
            process = processes[0]
            self.assertTrue(process.stdin.closed)
            self.assertTrue(process.stdout.closed)
            self.assertIsNotNone(process.poll())
        finally:
            for process in processes:
                isolation_module._kill_process(process)
                process.stdin.close()
                process.stdout.close()

    def test_signing_authority_object_is_not_present_in_the_fresh_interpreter(self):
        taken = self.taken(
            self.runner(AuthorityProbeWorker()).execute(self.permit_for(self.handoff), now=110)
        )
        self.assertTrue(taken.succeeded)
        self.assertEqual(taken.output, {"text": "authority-absent"})

    def test_worker_cannot_walk_parent_caller_frames_to_the_signing_authority(self):
        isolated = self.taken(
            self.runner(CallerFrameProbeWorker()).execute(
                self.permit_for(self.handoff), now=110)
        )
        self.assertTrue(isolated.succeeded)
        self.assertEqual(isolated.output, {"text": "caller-authority-absent"})

        # This is the exploit the composition root must not use: in-process
        # execution exposes a WorkerRunner in the caller chain to worker code.
        direct_wire = WorkerRunner(
            CallerFrameProbeWorker(), authority=self.authority
        ).execute(self.permit_for(self.handoff), now=110)
        direct = self.taken(direct_wire)
        self.assertEqual(direct.output, {"text": "caller-authority-present"})

    def test_network_creation_is_denied_and_recorded(self):
        taken = self.taken(self.runner(NetworkWorker()).execute(self.permit_for(self.handoff), now=110))
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, "ISOLATION_VIOLATED")
        self.assertIsNone(taken.output)

    def test_a_write_outside_the_temporary_directory_is_denied(self):
        with tempfile.TemporaryDirectory() as outside:
            target = os.path.join(outside, "escape.txt")
            taken = self.taken(
                self.runner(OutsideWriteWorker(target)).execute(self.permit_for(self.handoff), now=110)
            )
            self.assertFalse(os.path.exists(target))
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, "ISOLATION_VIOLATED")

    def test_a_write_inside_the_temporary_directory_is_allowed_then_cleaned_up(self):
        taken = self.taken(
            self.runner(InsideWriteWorker()).execute(self.permit_for(self.handoff), now=110)
        )
        self.assertTrue(taken.succeeded)
        sandbox_path = taken.output["text"]
        self.assertFalse(
            os.path.exists(sandbox_path),
            "the per-job temporary directory must be removed before execute returns",
        )

    def test_process_spawn_is_denied(self):
        taken = self.taken(self.runner(SpawnWorker()).execute(self.permit_for(self.handoff), now=110))
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, "ISOLATION_VIOLATED")

    def test_a_spawn_below_the_audit_hook_is_killed_by_the_kernel(self):
        """`_posixsubprocess` raises no audit event, so the hook never sees this
        spawn. The seccomp filter does: the kernel kills the child at the
        syscall, before a shell exists that could write anything, and the
        kill cannot be caught by the worker.
        """
        with tempfile.TemporaryDirectory() as outside:
            target = os.path.join(outside, "escape.txt")
            taken = self.taken(
                self.runner(NativeSpawnWorker(target)).execute(self.permit_for(self.handoff), now=110)
            )
            self.assertFalse(os.path.exists(target))
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, "ISOLATION_VIOLATED")
        self.assertIsNone(taken.output)

    def test_a_worker_may_still_start_a_thread(self):
        taken = self.taken(
            self.runner(ThreadWorker()).execute(self.permit_for(self.handoff), now=110)
        )
        self.assertTrue(taken.succeeded)
        self.assertEqual(taken.output, {"text": "the quick brown fox"})

    def test_a_read_outside_the_temporary_directory_is_allowed_and_this_is_the_boundary(self):
        """Held open (SECURITY.md): only `/proc`, `/sys` and `/dev` are refused
        to a reading worker. Anything else the service user can read, a worker
        can read and hand back as its output, which goes to the client.
        """
        with tempfile.TemporaryDirectory() as outside:
            target = os.path.join(outside, "host-file.txt")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("CANARY-OUTSIDE-THE-SANDBOX")
            taken = self.taken(
                self.runner(OutsideReadWorker(target)).execute(self.permit_for(self.handoff), now=110)
            )
        self.assertTrue(taken.succeeded)
        self.assertEqual(taken.output, {"text": "CANARY-OUTSIDE-THE-SANDBOX"})

    def test_parent_proc_environment_cannot_be_read(self):
        taken = self.taken(
            self.runner(ParentProcReadWorker()).execute(self.permit_for(self.handoff), now=110)
        )
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, "ISOLATION_VIOLATED")

    def test_ctypes_native_loader_is_denied(self):
        taken = self.taken(self.runner(CtypesWorker()).execute(self.permit_for(self.handoff), now=110))
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, "ISOLATION_VIOLATED")

    def test_filesystem_mutation_api_is_denied_even_inside_the_sandbox(self):
        taken = self.taken(
            self.runner(FilesystemMutationWorker()).execute(self.permit_for(self.handoff), now=110)
        )
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, "ISOLATION_VIOLATED")

    def test_worker_cannot_raise_or_replace_resource_limits(self):
        taken = self.taken(
            self.runner(LimitTamperWorker()).execute(self.permit_for(self.handoff), now=110)
        )
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, "ISOLATION_VIOLATED")

    def test_wall_clock_timeout_kills_the_child_and_returns_a_signed_failure(self):
        limits = IsolationLimits(wall_seconds=0.1)
        taken = self.taken(
            self.runner(SlowWorker(), limits).execute(self.permit_for(self.handoff), now=110)
        )
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, "RESOURCE_EXHAUSTED")
        self.assertIsNone(taken.output)

    def test_resource_limits_are_visible_inside_the_child(self):
        limits = IsolationLimits(
            wall_seconds=2,
            cpu_seconds=2,
            memory_bytes=256 * 1024 * 1024,
            max_file_bytes=8192,
            max_open_files=32,
        )
        taken = self.taken(
            self.runner(LimitsWorker(), limits).execute(self.permit_for(self.handoff), now=110)
        )
        self.assertTrue(taken.succeeded)
        self.assertEqual(
            taken.output["text"],
            "cpu=2;memory=268435456;file=8192;open=32",
        )

    def test_an_exception_becomes_worker_failed_without_leaking_its_text(self):
        wire = self.runner(ExplodingWorker()).execute(self.permit_for(self.handoff), now=110)
        self.assertNotIn(b"EXCEPTION-TEXT-MUST-STAY-IN-THE-CHILD", wire)
        taken = self.taken(wire)
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, "WORKER_FAILED")

    def test_invalid_output_still_uses_the_parent_side_output_contract(self):
        taken = self.taken(
            self.runner(ReturningWorker({"other": "not text"})).execute(
                self.permit_for(self.handoff), now=110
            )
        )
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, "OUTPUT_REJECTED")


if __name__ == "__main__":
    unittest.main()
