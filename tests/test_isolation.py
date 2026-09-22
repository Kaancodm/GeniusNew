import os
import tempfile
import time
import unittest
from unittest.mock import patch

from geniusnew.approvals import ApprovalStore
from geniusnew.contracts import ContractError, Grant, Policy, canonical, issue, validate
from geniusnew.gateway import Gateway
import geniusnew.isolation as isolation_module
import geniusnew.isolation_child as isolation_child_module
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
        self.gateway = Gateway(
            gateway_id="gateway-test",
            integrity_key=self.integrity_key,
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
            authority=self.authority,
            now=now,
        )

    def test_reference_worker_matches_the_in_process_contract(self):
        isolated = self.runner(DeterministicSummarizer()).execute(self.permit_for(self.handoff), now=110)
        direct = WorkerRunner(
            DeterministicSummarizer(), authority=self.authority
        ).execute(self.permit_for(self.handoff), now=110)
        self.assertEqual(isolated, direct)
        self.assertTrue(self.taken(isolated).succeeded)

    def test_child_pipes_are_closed_after_a_completed_run(self):
        started = []
        original_popen = isolation_module.subprocess.Popen

        def track_popen(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            started.append(process)
            return process

        with patch.object(isolation_module.subprocess, "Popen", side_effect=track_popen):
            self.runner(DeterministicSummarizer()).execute(self.permit_for(self.handoff), now=110)

        self.assertEqual(len(started), 1)
        self.assertTrue(started[0].stdin.closed)
        self.assertTrue(started[0].stdout.closed)

    def test_signing_authority_object_is_not_present_in_the_fresh_interpreter(self):
        taken = self.taken(
            self.runner(AuthorityProbeWorker()).execute(self.permit_for(self.handoff), now=110)
        )
        self.assertTrue(taken.succeeded)
        self.assertEqual(taken.output, {"text": "authority-absent"})

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
