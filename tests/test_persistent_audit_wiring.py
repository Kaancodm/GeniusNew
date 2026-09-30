"""B5 must recover before runners and keep the previously stored signature."""

import unittest
from unittest.mock import Mock, patch

from geniusnew.audit import AuditAuthority
from geniusnew.audit_chain import AuditAnchor, AuditChain
from geniusnew.contracts import ContractError
from geniusnew.keys import derive_keys
from geniusnew.results import WorkerAuthority
from geniusnew.wiring import build
from geniusnew.workers import DeterministicSummarizer, WorkerRunner
from tests import test_audit_store
from tests.test_end_to_end import API_KEY, ROOT_SECRET, Fixture


class StoredChain(AuditChain):
    def snapshot(self, authority):
        return self.saved_head, self.records


class PersistentAuditWiringTest(Fixture, unittest.TestCase):
    def options(self):
        return dict(root_secret=ROOT_SECRET, policy=self.policy_for(),
                    api_keys={API_KEY: "subject-demo"},
                    workers=(DeterministicSummarizer(),), anchor=AuditAnchor())

    def persisted(self, count=2):
        fixture = test_audit_store.StoredAuditSnapshotTest()
        fixture.setUp()
        authority = AuditAuthority(audit_key=derive_keys(ROOT_SECRET).audit_key)
        chain = StoredChain()
        for _ in range(count):
            chain.append(fixture.event)
        chain.saved_head = AuditChain.head(chain, authority)
        return chain, authority

    def test_corrupt_audit_storage_refuses_before_runner_construction(self):
        factory = Mock(side_effect=ContractError("stored audit has an unsigned suffix"))
        runner = Mock(side_effect=AssertionError("runner constructed"))
        with self.assertRaisesRegex(ContractError, "unsigned suffix"):
            build(**self.options(), runner_factory=runner, audit_chain_factory=factory)
        factory.assert_called_once()
        runner.assert_not_called()

    def test_factory_configuration_is_validated_before_runner_construction(self):
        runner = Mock(side_effect=AssertionError("runner constructed"))
        for factory, reason in ((object(), "must be callable"),
                                (lambda audit: object(), "return an AuditChain")):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ContractError, reason):
                    build(**self.options(), runner_factory=runner,
                          audit_chain_factory=factory)
        runner.assert_not_called()

    def test_stored_head_recovery_does_not_resign_and_precedes_runner_construction(self):
        chain, authority = self.persisted()
        options = self.options()
        keys = derive_keys(ROOT_SECRET)
        worker_authority = WorkerAuthority(result_key=keys.result_key,
                                           integrity_key=keys.integrity_key)
        anchor = options["anchor"]

        def runner(worker):
            self.assertEqual(anchor.committed, (2, chain.saved_head.head_hash))
            return WorkerRunner(worker, authority=worker_authority)

        with patch.object(AuditAuthority, "sign", side_effect=AssertionError("re-signed")):
            service = build(**options, audit_chain_factory=lambda audit: chain,
                            runner_factory=runner)
            self.addCleanup(service.close)
            self.assertEqual(service.head(), chain.saved_head)

    def test_anchor_ahead_or_wrong_acknowledgement_refuses_before_runners(self):
        chain, authority = self.persisted()
        class BadAcknowledgement(AuditAnchor):
            def commit(self, head, records, *, authority):
                return (head.count, "f" * 64)

        for anchor in (BadAcknowledgement(), AuditAnchor.resumed(
                AuditChain.head(self.persisted(3)[0], authority), authority=authority.verifier())):
            options = self.options()
            options["anchor"] = anchor
            runner = Mock(side_effect=AssertionError("runner constructed"))
            with self.subTest(anchor=type(anchor).__name__):
                with self.assertRaises(ContractError):
                    build(**options, audit_chain_factory=lambda audit: chain,
                          runner_factory=runner)
            runner.assert_not_called()
