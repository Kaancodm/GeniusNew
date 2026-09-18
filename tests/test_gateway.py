import hashlib
from dataclasses import replace
import unittest

from geniusnew.approvals import ApprovalStore, create_scope
from geniusnew.contracts import ContractError, Grant, Policy, issue, validate
from geniusnew.gateway import DispatchPermit, Gateway, handoff_from_permit
from geniusnew.results import WorkerAuthority, accept
from geniusnew.workers import DeterministicSummarizer, WorkerRunner


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.key = b"phase-2-test-integrity-key-32bytes"
        self.store = ApprovalStore(token_source=lambda: b"a" * 32)
        self.gateway = Gateway(
            gateway_id="gateway-test",
            integrity_key=self.key,
            approval_store=self.store,
        )
        self.policy = self.policy_for(requires_approval=False)
        self.wire = self.issue(self.policy)

    def policy_for(self, *, requires_approval):
        grant = Grant(
            "subject-demo", "user-demo", "worker-demo", "basic",
            ("summarize",), "isolated", requires_approval,
        )
        return Policy(
            "policy-v1", "orchestrator-demo", 60,
            ("summarize",), ("isolated",), (grant,),
        )

    def issue(self, policy, *, job_id="job-demo", now=100, text="hello gateway"):
        return issue(
            {"text": text},
            subject="subject-demo",
            job_id=job_id,
            policy=policy,
            integrity_key=self.key,
            now=now,
        )

    def admit(self, wire=None, *, policy=None, job_id="job-demo", now=101,
              approval_token=None):
        return self.gateway.admit(
            self.wire if wire is None else wire,
            subject="subject-demo",
            job_id=job_id,
            policy=self.policy if policy is None else policy,
            now=now,
            approval_token=approval_token,
        )

    def test_gateway_revalidates_raw_wire_and_mints_a_bound_permit(self):
        permit = self.admit()
        self.assertEqual(permit.gateway_id, "gateway-test")
        self.assertEqual(permit.admitted_at, 101)
        self.assertIsNone(permit.approval_record_hash)
        self.assertEqual(permit.handoff_sha256, hashlib.sha256(permit.handoff.to_bytes()).hexdigest())
        self.assertIs(handoff_from_permit(permit), permit.handoff)

    def test_worker_requires_gateway_permit_not_an_in_memory_handoff(self):
        handoff = validate(
            self.wire,
            subject="subject-demo",
            job_id="job-demo",
            policy=self.policy,
            integrity_key=self.key,
            now=101,
        )
        runner = WorkerRunner(
            DeterministicSummarizer(),
            authority=WorkerAuthority(result_key=b"a-separate-result-key-of-32bytes!"),
        )
        with self.assertRaisesRegex(ContractError, "gateway-minted"):
            runner.execute(handoff, now=110)

    def test_gateway_and_worker_complete_the_non_approval_path(self):
        permit = self.admit()
        authority = WorkerAuthority(result_key=b"a-separate-result-key-of-32bytes!")
        wire = WorkerRunner(
            DeterministicSummarizer(), authority=authority
        ).execute(permit, now=110)
        taken = accept(wire, handoff=permit.handoff, authority=authority, now=120)
        self.assertTrue(taken.succeeded)

    def test_tampered_wrong_job_wrong_subject_and_expired_wires_fail_closed(self):
        cases = [
            lambda: self.gateway.admit(
                self.wire.replace(b"hello", b"HELLO"),
                subject="subject-demo", job_id="job-demo",
                policy=self.policy, now=101,
            ),
            lambda: self.gateway.admit(
                self.wire, subject="subject-demo", job_id="job-other",
                policy=self.policy, now=101,
            ),
            lambda: self.gateway.admit(
                self.wire, subject="subject-other", job_id="job-demo",
                policy=self.policy, now=101,
            ),
            lambda: self.gateway.admit(
                self.wire, subject="subject-demo", job_id="job-demo",
                policy=self.policy, now=160,
            ),
        ]
        for call in cases:
            with self.subTest(call=call), self.assertRaises(ContractError):
                call()

    def test_already_validated_handoff_is_not_a_gateway_input(self):
        handoff = validate(
            self.wire,
            subject="subject-demo",
            job_id="job-demo",
            policy=self.policy,
            integrity_key=self.key,
            now=101,
        )
        with self.assertRaises(ContractError):
            self.gateway.admit(
                handoff, subject="subject-demo", job_id="job-demo",
                policy=self.policy, now=101,
            )

    def test_approval_required_handoff_needs_and_consumes_one_token(self):
        policy = self.policy_for(requires_approval=True)
        wire = self.issue(policy)
        scope = create_scope(
            wire,
            subject="subject-demo",
            job_id="job-demo",
            policy=policy,
            integrity_key=self.key,
            now=101,
        )
        grant = self.store.grant(scope, now=101, ttl_seconds=30)

        with self.assertRaisesRegex(ContractError, "required"):
            self.gateway.admit(
                wire, subject="subject-demo", job_id="job-demo",
                policy=policy, now=102,
            )

        permit = self.gateway.admit(
            wire,
            subject="subject-demo",
            job_id="job-demo",
            policy=policy,
            now=102,
            approval_token=grant.token,
        )
        self.assertIsNotNone(permit.approval_record_hash)
        self.assertEqual(permit.handoff.approval_state, "PENDING_APPROVAL")

        with self.assertRaises(ContractError):
            self.gateway.admit(
                wire,
                subject="subject-demo",
                job_id="job-demo",
                policy=policy,
                now=103,
                approval_token=grant.token,
            )

    def test_approval_for_another_handoff_cannot_authorize_this_one(self):
        policy = self.policy_for(requires_approval=True)
        first = self.issue(policy, job_id="job-first")
        second = self.issue(policy, job_id="job-second")
        scope = create_scope(
            first, subject="subject-demo", job_id="job-first", policy=policy,
            integrity_key=self.key, now=101,
        )
        token = self.store.grant(scope, now=101, ttl_seconds=30).token
        with self.assertRaisesRegex(ContractError, "scope"):
            self.gateway.admit(
                second,
                subject="subject-demo",
                job_id="job-second",
                policy=policy,
                now=102,
                approval_token=token,
            )

    def test_unexpected_approval_token_is_refused_for_low_risk_handoff(self):
        with self.assertRaisesRegex(ContractError, "not allowed"):
            self.admit(approval_token=b"x" * 32)

    def test_permit_detects_payload_mutation_after_gateway_admission(self):
        permit = self.admit()
        permit.handoff.payload["text"] = "mutated after admission"
        with self.assertRaisesRegex(ContractError, "no longer matches"):
            handoff_from_permit(permit)

    def test_dispatch_permit_cannot_be_minted_by_callers(self):
        handoff = validate(
            self.wire,
            subject="subject-demo",
            job_id="job-demo",
            policy=self.policy,
            integrity_key=self.key,
            now=101,
        )
        with self.assertRaisesRegex(ContractError, "minted"):
            DispatchPermit(
                handoff=handoff,
                handoff_sha256="0" * 64,
                gateway_id="gateway-test",
                admitted_at=101,
                approval_record_hash=None,
            )

    def test_dispatch_permit_internal_fields_fail_closed(self):
        permit = self.admit()
        for handoff in (None, "handoff", 42, {}):
            with self.subTest(handoff=type(handoff)), self.assertRaises(ContractError):
                replace(permit, handoff=handoff)
        for digest in ("", "z" * 64, "A" * 64, "a" * 63, None, 42):
            with self.subTest(digest=digest), self.assertRaises(ContractError):
                replace(permit, handoff_sha256=digest)
        for admitted_at in (None, "101", 101.0, True):
            with self.subTest(admitted_at=admitted_at), self.assertRaises(ContractError):
                replace(permit, admitted_at=admitted_at)
        for admitted_at in (permit.handoff.issued_at - 1, permit.handoff.expires_at):
            with self.subTest(admitted_at=admitted_at), self.assertRaises(ContractError):
                replace(permit, admitted_at=admitted_at)
        for receipt_hash in ("", "z" * 64, "A" * 64, "a" * 63, 42):
            with self.subTest(receipt_hash=receipt_hash), self.assertRaises(ContractError):
                replace(permit, approval_record_hash=receipt_hash)

    def test_admit_requires_a_real_policy_object(self):
        for policy in (None, "policy", 42, {}, self.policy.__dict__):
            with self.subTest(policy=type(policy)), self.assertRaisesRegex(
                ContractError, "policy is invalid"
            ):
                self.gateway.admit(
                    self.wire,
                    subject="subject-demo",
                    job_id="job-demo",
                    policy=policy,
                    now=101,
                )

    def test_gateway_configuration_fails_closed(self):
        for gateway_id in ("", "UPPER", "with space", None, 42):
            with self.subTest(gateway_id=gateway_id), self.assertRaises(ContractError):
                Gateway(
                    gateway_id=gateway_id,
                    integrity_key=self.key,
                    approval_store=self.store,
                )
        for key in (None, b"", b"short", "not-bytes"):
            with self.subTest(key=key), self.assertRaises(ContractError):
                Gateway(
                    gateway_id="gateway-test",
                    integrity_key=key,
                    approval_store=self.store,
                )
        for store in (None, {}, "store"):
            with self.subTest(store=store), self.assertRaises(ContractError):
                Gateway(
                    gateway_id="gateway-test",
                    integrity_key=self.key,
                    approval_store=store,
                )


if __name__ == "__main__":
    unittest.main()
