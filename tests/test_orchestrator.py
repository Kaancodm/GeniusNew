import unittest

from geniusnew.approvals import ApprovalStore, create_scope
from geniusnew.contracts import ContractError, Grant, Policy, validate
from geniusnew.gateway import Gateway
from geniusnew.orchestrator import Orchestrator, WorkerEndpoint
from geniusnew.results import WorkerAuthority, accept
from geniusnew.workers import DeterministicSummarizer, Worker, WorkerRunner


class OtherToolWorker(Worker):
    tool = "translate"

    def run(self, payload):
        return {"text": payload["text"]}


class OrchestratorTest(unittest.TestCase):
    def setUp(self):
        self.key = b"phase-2-test-integrity-key-32bytes"
        self.store = ApprovalStore(token_source=lambda: b"a" * 32)
        self.gateway = Gateway(
            gateway_id="gateway-test",
            integrity_key=self.key,
            approval_store=self.store,
        )
        self.result_authority = WorkerAuthority(
            result_key=b"a-separate-result-key-of-32bytes!"
        )
        self.endpoint = WorkerEndpoint(
            "worker-demo",
            WorkerRunner(DeterministicSummarizer(), authority=self.result_authority),
        )
        self.orchestrator = Orchestrator(
            orchestrator_id="orchestrator-demo",
            gateway=self.gateway,
            workers=(self.endpoint,),
        )
        self.policy = self.policy_for()

    def policy_for(self, *, worker_agent_id="worker-demo", tools=("summarize",),
                   requires_approval=False, orchestrator_id="orchestrator-demo"):
        grant = Grant(
            "subject-demo", "user-demo", worker_agent_id, "basic",
            tools, "isolated", requires_approval,
        )
        return Policy(
            "policy-v1", orchestrator_id, 60,
            ("summarize", "translate"), ("isolated",), (grant,),
        )

    def admit(self, *, policy=None, job_id="job-demo", now=100,
              request=None):
        return self.orchestrator.admit(
            {"text": "the quick brown fox"} if request is None else request,
            subject="subject-demo",
            job_id=job_id,
            policy=self.policy if policy is None else policy,
            now=now,
        )

    def test_admission_derives_identity_and_capabilities_only_from_policy(self):
        wire = self.admit()
        handoff = validate(
            wire,
            subject="subject-demo",
            job_id="job-demo",
            policy=self.policy,
            integrity_key=self.key,
            now=101,
        )
        self.assertEqual(handoff.user_id, "user-demo")
        self.assertEqual(handoff.worker_agent_id, "worker-demo")
        self.assertEqual(handoff.tools, ("summarize",))
        self.assertEqual(handoff.orchestrator_id, "orchestrator-demo")

    def test_non_approval_job_runs_through_gateway_and_selected_worker(self):
        wire = self.admit()
        result_wire = self.orchestrator.dispatch(
            wire,
            subject="subject-demo",
            job_id="job-demo",
            policy=self.policy,
            now=110,
        )
        handoff = validate(
            wire,
            subject="subject-demo",
            job_id="job-demo",
            policy=self.policy,
            integrity_key=self.key,
            now=110,
        )
        result = accept(
            result_wire,
            handoff=handoff,
            authority=self.result_authority,
            now=120,
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(result.reason_code, "WORK_COMPLETED")

    def test_routing_is_exactly_by_trusted_worker_id(self):
        missing = self.policy_for(worker_agent_id="worker-missing")
        wire = self.admit(policy=missing)
        with self.assertRaisesRegex(ContractError, "unconfigured worker"):
            self.orchestrator.dispatch(
                wire,
                subject="subject-demo",
                job_id="job-demo",
                policy=missing,
                now=110,
            )

    def test_misconfigured_endpoint_tool_is_refused_before_gateway(self):
        translate_endpoint = WorkerEndpoint(
            "worker-demo",
            WorkerRunner(OtherToolWorker(), authority=self.result_authority),
        )
        orchestrator = Orchestrator(
            orchestrator_id="orchestrator-demo",
            gateway=self.gateway,
            workers=(translate_endpoint,),
        )
        wire = orchestrator.admit(
            {"text": "hello"},
            subject="subject-demo",
            job_id="job-demo",
            policy=self.policy,
            now=100,
        )
        with self.assertRaisesRegex(ContractError, "not granted"):
            orchestrator.dispatch(
                wire,
                subject="subject-demo",
                job_id="job-demo",
                policy=self.policy,
                now=110,
            )

    def test_required_approval_is_consumed_by_gateway_not_orchestrator(self):
        policy = self.policy_for(requires_approval=True)
        wire = self.admit(policy=policy)
        scope = create_scope(
            wire,
            subject="subject-demo",
            job_id="job-demo",
            policy=policy,
            integrity_key=self.key,
            now=101,
        )
        token = self.store.grant(scope, now=101, ttl_seconds=30).token

        with self.assertRaisesRegex(ContractError, "required"):
            self.orchestrator.dispatch(
                wire,
                subject="subject-demo",
                job_id="job-demo",
                policy=policy,
                now=102,
            )

        result_wire = self.orchestrator.dispatch(
            wire,
            subject="subject-demo",
            job_id="job-demo",
            policy=policy,
            now=102,
            approval_token=token,
        )
        self.assertIsInstance(result_wire, bytes)

        with self.assertRaises(ContractError):
            self.orchestrator.dispatch(
                wire,
                subject="subject-demo",
                job_id="job-demo",
                policy=policy,
                now=103,
                approval_token=token,
            )

    def test_missing_worker_is_checked_before_consuming_approval(self):
        policy = self.policy_for(
            worker_agent_id="worker-missing",
            requires_approval=True,
        )
        wire = self.admit(policy=policy)
        scope = create_scope(
            wire,
            subject="subject-demo",
            job_id="job-demo",
            policy=policy,
            integrity_key=self.key,
            now=101,
        )
        grant = self.store.grant(scope, now=101, ttl_seconds=30)

        with self.assertRaisesRegex(ContractError, "unconfigured worker"):
            self.orchestrator.dispatch(
                wire,
                subject="subject-demo",
                job_id="job-demo",
                policy=policy,
                now=102,
                approval_token=grant.token,
            )

        self.assertEqual(
            self.store.consume(grant.token, scope, now=102).state,
            "CONSUMED",
            "routing failure must happen before the gateway burns approval",
        )

    def test_gateway_still_rejects_tampered_wire_after_orchestrator_admission(self):
        wire = self.admit()
        with self.assertRaises(ContractError):
            self.orchestrator.dispatch(
                wire.replace(b"quick", b"QUICK"),
                subject="subject-demo",
                job_id="job-demo",
                policy=self.policy,
                now=110,
            )

    def test_policy_must_name_this_orchestrator(self):
        wrong = self.policy_for(orchestrator_id="orchestrator-other")
        for call in (
            lambda: self.admit(policy=wrong),
            lambda: self.orchestrator.dispatch(
                b"wire",
                subject="subject-demo",
                job_id="job-demo",
                policy=wrong,
                now=110,
            ),
        ):
            with self.subTest(call=call), self.assertRaisesRegex(
                ContractError, "does not name this orchestrator"
            ):
                call()

    def test_worker_registry_is_copied_and_deterministic(self):
        workers = [self.endpoint]
        orchestrator = Orchestrator(
            orchestrator_id="orchestrator-demo",
            gateway=self.gateway,
            workers=workers,
        )
        workers.clear()
        self.assertEqual(orchestrator.worker_ids, ("worker-demo",))
        wire = orchestrator.admit(
            {"text": "hello"},
            subject="subject-demo",
            job_id="job-demo",
            policy=self.policy,
            now=100,
        )
        self.assertIsInstance(
            orchestrator.dispatch(
                wire,
                subject="subject-demo",
                job_id="job-demo",
                policy=self.policy,
                now=110,
            ),
            bytes,
        )

    def test_constructor_configuration_fails_closed(self):
        for orchestrator_id in ("", "UPPER", "with space", None, 42):
            with self.subTest(orchestrator_id=orchestrator_id), self.assertRaises(
                ContractError
            ):
                Orchestrator(
                    orchestrator_id=orchestrator_id,
                    gateway=self.gateway,
                    workers=(self.endpoint,),
                )
        for gateway in (None, "gateway", 42, {}):
            with self.subTest(gateway=type(gateway)), self.assertRaisesRegex(
                ContractError, "gateway"
            ):
                Orchestrator(
                    orchestrator_id="orchestrator-demo",
                    gateway=gateway,
                    workers=(self.endpoint,),
                )
        for workers in (None, 42):
            with self.subTest(workers=workers), self.assertRaisesRegex(
                ContractError, "iterable"
            ):
                Orchestrator(
                    orchestrator_id="orchestrator-demo",
                    gateway=self.gateway,
                    workers=workers,
                )
        with self.assertRaisesRegex(ContractError, "must not be empty"):
            Orchestrator(
                orchestrator_id="orchestrator-demo",
                gateway=self.gateway,
                workers=(),
            )
        with self.assertRaisesRegex(ContractError, "WorkerEndpoint"):
            Orchestrator(
                orchestrator_id="orchestrator-demo",
                gateway=self.gateway,
                workers=(self.endpoint, "not-endpoint"),
            )
        with self.assertRaisesRegex(ContractError, "unique"):
            Orchestrator(
                orchestrator_id="orchestrator-demo",
                gateway=self.gateway,
                workers=(self.endpoint, self.endpoint),
            )

    def test_worker_endpoint_configuration_fails_closed(self):
        for worker_id in ("", "UPPER", "with space", None, 42):
            with self.subTest(worker_id=worker_id), self.assertRaises(ContractError):
                WorkerEndpoint(worker_id, self.endpoint.runner)
        for runner in (None, "runner", 42, {}):
            with self.subTest(runner=type(runner)), self.assertRaisesRegex(
                ContractError, "WorkerRunner"
            ):
                WorkerEndpoint("worker-demo", runner)

    def test_policy_object_itself_is_required(self):
        for policy in (None, "policy", 42, {}):
            with self.subTest(policy=type(policy)), self.assertRaisesRegex(
                ContractError, "policy is invalid"
            ):
                self.orchestrator.admit(
                    {"text": "hello"},
                    subject="subject-demo",
                    job_id="job-demo",
                    policy=policy,
                    now=100,
                )


if __name__ == "__main__":
    unittest.main()
