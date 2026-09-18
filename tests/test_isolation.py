import os
import tempfile
import time
import unittest
from unittest.mock import patch

from geniusnew.contracts import ContractError, Grant, Policy, issue, validate
import geniusnew.isolation as isolation_module
from geniusnew.isolation import IsolationLimits, IsolatedWorkerRunner
from geniusnew.results import WorkerAuthority, accept
from geniusnew.workers import DeterministicSummarizer, Worker, WorkerRunner


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


class ParentProcReadWorker(Worker):
    tool = "summarize"

    def run(self, payload):
        with open(f"/proc/{os.getppid()}/environ", "rb") as handle:
            data = handle.read()
        return {"text": str(len(data))}


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

    @unittest.skipUnless(isolation_module._resource_supported(), "POSIX resource limits required")
    def test_runner_requires_a_limits_object(self):
        authority = WorkerAuthority(result_key=b"a-separate-result-key-of-32bytes!")
        with self.assertRaisesRegex(ContractError, "IsolationLimits"):
            IsolatedWorkerRunner(
                DeterministicSummarizer(), authority=authority, limits="not-limits"
            )

    @unittest.skipUnless(isolation_module._resource_supported(), "POSIX resource limits required")
    def test_runner_fails_closed_without_resource_limits(self):
        authority = WorkerAuthority(result_key=b"a-separate-result-key-of-32bytes!")
        with patch.object(isolation_module, "_resource_supported", return_value=False):
            with self.assertRaisesRegex(ContractError, "POSIX resource limits"):
                IsolatedWorkerRunner(DeterministicSummarizer(), authority=authority)

    def test_local_worker_classes_are_not_accepted_for_exec_isolation(self):
        class LocalWorker(Worker):
            tool = "summarize"

            def run(self, payload):
                return {"text": "no"}

        authority = WorkerAuthority(result_key=b"a-separate-result-key-of-32bytes!")
        with self.assertRaisesRegex(ContractError, "importable"):
            IsolatedWorkerRunner(LocalWorker(), authority=authority)

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


@unittest.skipUnless(isolation_module._resource_supported(), "POSIX resource limits required")
class ProcessIsolationTest(unittest.TestCase):
    def setUp(self):
        self.integrity_key = b"phase-2-test-integrity-key-32bytes"
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
            integrity_key=self.integrity_key,
            now=100,
        )
        self.handoff = validate(
            wire,
            subject="subject-demo",
            job_id="job-demo",
            policy=self.policy,
            integrity_key=self.integrity_key,
            now=101,
        )

    def runner(self, worker, limits=None):
        return IsolatedWorkerRunner(worker, authority=self.authority, limits=limits)

    def taken(self, wire, now=120):
        return accept(
            wire,
            handoff=self.handoff,
            authority=self.authority,
            now=now,
        )

    def test_reference_worker_matches_the_in_process_contract(self):
        isolated = self.runner(DeterministicSummarizer()).execute(self.handoff, now=110)
        direct = WorkerRunner(
            DeterministicSummarizer(), authority=self.authority
        ).execute(self.handoff, now=110)
        self.assertEqual(isolated, direct)
        self.assertTrue(self.taken(isolated).succeeded)

    def test_signing_authority_object_is_not_present_in_the_fresh_interpreter(self):
        taken = self.taken(
            self.runner(AuthorityProbeWorker()).execute(self.handoff, now=110)
        )
        self.assertTrue(taken.succeeded)
        self.assertEqual(taken.output, {"text": "authority-absent"})

    def test_network_creation_is_denied_and_recorded(self):
        taken = self.taken(self.runner(NetworkWorker()).execute(self.handoff, now=110))
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, "ISOLATION_VIOLATED")
        self.assertIsNone(taken.output)

    def test_a_write_outside_the_temporary_directory_is_denied(self):
        with tempfile.TemporaryDirectory() as outside:
            target = os.path.join(outside, "escape.txt")
            taken = self.taken(
                self.runner(OutsideWriteWorker(target)).execute(self.handoff, now=110)
            )
            self.assertFalse(os.path.exists(target))
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, "ISOLATION_VIOLATED")

    def test_a_write_inside_the_temporary_directory_is_allowed_then_cleaned_up(self):
        taken = self.taken(
            self.runner(InsideWriteWorker()).execute(self.handoff, now=110)
        )
        self.assertTrue(taken.succeeded)
        sandbox_path = taken.output["text"]
        self.assertFalse(
            os.path.exists(sandbox_path),
            "the per-job temporary directory must be removed before execute returns",
        )

    def test_process_spawn_is_denied(self):
        taken = self.taken(self.runner(SpawnWorker()).execute(self.handoff, now=110))
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, "ISOLATION_VIOLATED")

    def test_parent_proc_environment_cannot_be_read(self):
        taken = self.taken(
            self.runner(ParentProcReadWorker()).execute(self.handoff, now=110)
        )
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, "ISOLATION_VIOLATED")

    def test_worker_cannot_raise_or_replace_resource_limits(self):
        taken = self.taken(
            self.runner(LimitTamperWorker()).execute(self.handoff, now=110)
        )
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, "ISOLATION_VIOLATED")

    def test_wall_clock_timeout_kills_the_child_and_returns_a_signed_failure(self):
        limits = IsolationLimits(wall_seconds=0.1)
        taken = self.taken(
            self.runner(SlowWorker(), limits).execute(self.handoff, now=110)
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
            self.runner(LimitsWorker(), limits).execute(self.handoff, now=110)
        )
        self.assertTrue(taken.succeeded)
        self.assertEqual(
            taken.output["text"],
            "cpu=2;memory=268435456;file=8192;open=32",
        )

    def test_an_exception_becomes_worker_failed_without_leaking_its_text(self):
        wire = self.runner(ExplodingWorker()).execute(self.handoff, now=110)
        self.assertNotIn(b"EXCEPTION-TEXT-MUST-STAY-IN-THE-CHILD", wire)
        taken = self.taken(wire)
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, "WORKER_FAILED")

    def test_invalid_output_still_uses_the_parent_side_output_contract(self):
        taken = self.taken(
            self.runner(ReturningWorker({"other": "not text"})).execute(
                self.handoff, now=110
            )
        )
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, "OUTPUT_REJECTED")


if __name__ == "__main__":
    unittest.main()
