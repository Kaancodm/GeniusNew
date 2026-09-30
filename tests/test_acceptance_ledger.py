"""Gate B3: genuine artifacts survive restart; corruption never opens a listener."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import unittest
from unittest.mock import Mock, patch

import psycopg

from geniusnew.audit_chain import AuditAnchor
from geniusnew.contracts import ContractError, canonical, validate
from geniusnew.database import PostgresAcceptanceLedger, migrate, open_database
from geniusnew.verifier import Rejected, ResultVerifier
from geniusnew.wiring import build
from geniusnew.workers import DeterministicSummarizer
from postgres_support import PostgresDatabase
from test_verifier import Fixture, ROOT_SECRET


class PersistentAcceptanceTest(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.db = PostgresDatabase()
        self.addCleanup(self.db.close)
        migrate(self.db.owner_dsn)
        self.digest = sha256(self.wire).hexdigest()
        self.execute("INSERT INTO job_ledger VALUES "
                     "('job-demo','subject-demo',%s,'EXECUTION_COMMITTED',100,100,110,160)",
                     (self.digest,))
        self.store = self.ledger()
        self.verifier = self.persistent(self.store)

    def ledger(self):
        connection = self.db.connect(runtime=True)
        self.addCleanup(connection.close)
        return PostgresAcceptanceLedger(connection)

    def persistent(self, ledger):
        return ResultVerifier(verifier_id='verifier-1', handoff_verifier=self.signer.verifier(),
                              worker_verifier=self.worker_authority.verifier(),
                              acceptance_ledger=ledger)

    def execute(self, query, parameters=None):
        with self.db.connect() as owner:
            cursor = owner.execute(query, parameters)
            return cursor.fetchall() if cursor.description else None

    def rows(self):
        return self.execute("SELECT handoff_sha256, job_id, handoff_wire, result_sha256, "
                            "result_wire, accepted_at FROM acceptance_ledger")

    def check(self, ledger=None):
        (ledger or self.store).check(policy=self.policy,
                                   handoff_verifier=self.signer.verifier(),
                                   worker_verifier=self.worker_authority.verifier())

    def corrupt(self, statements):
        # Only this disposable fixture. Model owner damage without weakening
        # the runtime or any shared role's privileges.
        with self.db.connect() as owner, owner.transaction():
            owner.execute("SET LOCAL session_replication_role = replica")
            for query, parameters in statements:
                owner.execute(query, parameters)

    def test_a_restarted_verifier_does_not_accept_the_same_result_again(self):
        wire = self.result_for()
        self.assertTrue(self.take(wire).succeeded)
        self.check(self.ledger())
        with self.assertRaises(Rejected) as caught:
            self.take(wire, verifier=self.persistent(self.ledger()))
        self.assertEqual(caught.exception.reason_code, 'RESULT_ALREADY_ACCEPTED')
        self.assertEqual(self.rows(), [(self.digest, 'job-demo', self.wire,
                                      sha256(wire).hexdigest(), wire, 120)])
        self.assertEqual(self.execute("SELECT state,updated_at FROM job_ledger"),
                         [('COMPLETED', 120)])

    def test_two_instances_accept_exactly_once(self):
        wire = self.result_for()
        verifiers = [self.persistent(self.ledger()) for _ in range(2)]

        def take(verifier):
            try:
                self.take(wire, verifier=verifier)
                return 'ACCEPTED'
            except Rejected as refusal:
                return refusal.reason_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(take, verifiers))
        self.assertCountEqual(results, ['ACCEPTED', 'RESULT_ALREADY_ACCEPTED'])
        self.assertEqual(len(self.rows()), 1)
        self.check()

    def test_invalid_result_does_not_burn_acceptance_or_complete_the_job(self):
        with self.assertRaises(Rejected):
            self.take(self.result_for()[:-1])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.execute("SELECT state FROM job_ledger"), [('EXECUTION_COMMITTED',)])
        self.assertTrue(self.take().succeeded)

    def test_acceptance_refuses_a_different_handoff_for_the_same_job(self):
        wire = self.issue(text='another payload')
        handoff = validate(wire, subject='subject-demo', job_id='job-demo', policy=self.policy,
                           verifier=self.signer.verifier(), now=101)
        with self.assertRaisesRegex(ContractError, 'acceptance ledger is unavailable'):
            self.take(self.result_for(handoff), handoff_wire=wire)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.execute("SELECT state FROM job_ledger"), [('EXECUTION_COMMITTED',)])
        self.assertTrue(self.take().succeeded)

    def test_acceptance_requires_exactly_execution_committed(self):
        for state in ('PENDING_APPROVAL', 'RESERVED', 'REFUSED', 'COMPLETED'):
            with self.subTest(state=state):
                self.corrupt([("UPDATE job_ledger SET state=%s,reserved_at=%s",
                               (state, None if state == 'PENDING_APPROVAL' else 100))])
                with self.assertRaisesRegex(ContractError, 'acceptance ledger is unavailable'):
                    self.take()
                self.assertEqual(self.rows(), [])
                self.assertEqual(self.execute("SELECT state FROM job_ledger"), [(state,)])

    def test_a_failed_commit_rolls_back_acceptance_and_completion(self):
        self.execute("CREATE FUNCTION public.fail_commit() RETURNS trigger LANGUAGE plpgsql AS "
                     "$$ BEGIN RAISE EXCEPTION 'fixture commit refused' USING ERRCODE='23514'; END; $$")
        self.execute("CREATE CONSTRAINT TRIGGER fail_commit AFTER INSERT ON acceptance_ledger "
                     "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fail_commit()")
        with self.assertRaisesRegex(ContractError, 'acceptance ledger is unavailable') as caught:
            self.take()
        self.assertNotIn('fixture commit refused', str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.execute("SELECT state FROM job_ledger"), [('EXECUTION_COMMITTED',)])

    def test_a_missing_completion_trigger_never_reports_acceptance_success(self):
        self.execute("ALTER TABLE acceptance_ledger DISABLE TRIGGER acceptance_complete")
        with self.assertRaisesRegex(ContractError, 'did not complete the bound job'):
            self.take()
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.execute("SELECT state FROM job_ledger"), [('EXECUTION_COMMITTED',)])

    def test_a_closed_database_has_no_memory_fallback(self):
        self.store._connection.close()
        for _ in range(2):
            with self.assertRaisesRegex(ContractError, 'acceptance ledger is unavailable'):
                self.take()
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.execute("SELECT state FROM job_ledger"), [('EXECUTION_COMMITTED',)])

    def test_a_caller_transaction_cannot_hide_a_successful_acceptance(self):
        with self.store._connection.transaction(force_rollback=True):
            with self.assertRaisesRegex(ContractError, 'cannot join an existing transaction'):
                self.take()
        self.assertEqual(self.rows(), [])
        self.assertTrue(self.take().succeeded)

    def test_the_persistent_ledger_has_no_process_local_entry_limit(self):
        with patch('geniusnew.verifier._MAX_ACCEPTED', 0):
            self.assertTrue(self.take().succeeded)

    def test_each_changed_stored_wire_byte_is_refused(self):
        result = self.result_for()
        self.take(result)
        for column, original in (('handoff_wire', self.wire), ('result_wire', result)):
            with self.subTest(column=column):
                self.execute('UPDATE acceptance_ledger SET ' + column + '=%s', (original[:-1] + b'!',))
                with self.assertRaisesRegex(ContractError, 'wire digest mismatch'):
                    self.check()
                self.execute('UPDATE acceptance_ledger SET ' + column + '=%s', (original,))
        self.check()

    def test_recomputed_digests_cannot_hide_a_changed_signature_or_payload(self):
        result = self.result_for()
        self.take(result)
        changed = json.loads(self.wire)
        changed['payload']['text'] = 'changed payload'
        changed['payload_sha256'] = sha256(canonical(changed['payload'])).hexdigest()
        changed_wire = canonical(changed)
        changed_digest = sha256(changed_wire).hexdigest()
        self.corrupt([
            ("UPDATE job_ledger SET handoff_sha256=%s", (changed_digest,)),
            ("UPDATE acceptance_ledger SET handoff_sha256=%s,handoff_wire=%s",
             (changed_digest, changed_wire))])
        with self.assertRaisesRegex(ContractError, 'signature is invalid'):
            self.check()
        self.corrupt([
            ("UPDATE job_ledger SET handoff_sha256=%s", (self.digest,)),
            ("UPDATE acceptance_ledger SET handoff_sha256=%s,handoff_wire=%s",
             (self.digest, self.wire))])
        changed_result = result.replace(b'a summary', b'z summary')
        self.execute("UPDATE acceptance_ledger SET result_sha256=%s,result_wire=%s",
                     (sha256(changed_result).hexdigest(), changed_result))
        with self.assertRaisesRegex(ContractError, 'signature is invalid'):
            self.check()

    def test_a_valid_result_bound_to_another_handoff_is_refused_at_start(self):
        self.take()
        other_wire = self.issue(text='other handoff')
        other_digest = sha256(other_wire).hexdigest()
        self.corrupt([
            ("UPDATE job_ledger SET handoff_sha256=%s", (other_digest,)),
            ("UPDATE acceptance_ledger SET handoff_sha256=%s,handoff_wire=%s",
             (other_digest, other_wire))])
        with self.assertRaisesRegex(ContractError, 'does not match the handoff'):
            self.check()

    def test_start_rechecks_both_directions_even_if_the_owner_bypasses_constraints(self):
        self.take()
        self.corrupt([("UPDATE job_ledger SET handoff_sha256=%s", ('a' * 64,))])
        with self.assertRaisesRegex(ContractError, 'completed jobs do not match'):
            self.check()
        with self.assertRaisesRegex(ContractError, 'completed jobs do not match'):
            with open_database(self.db.runtime_dsn):
                self.fail('the broken binding was accepted')

    def test_start_refuses_each_invalid_acceptance_time(self):
        self.take()
        for accepted_at in (0, 99, 160, 4102444801):
            with self.subTest(accepted_at=accepted_at):
                self.execute("UPDATE acceptance_ledger SET accepted_at=%s", (accepted_at,))
                with self.assertRaisesRegex(ContractError, 'invalid acceptance time'):
                    self.check()
        self.execute("UPDATE acceptance_ledger SET accepted_at=120")
        self.corrupt([("UPDATE job_ledger SET updated_at=121", None)])
        with self.assertRaisesRegex(ContractError, 'invalid acceptance time'):
            self.check()

    def test_start_refuses_a_different_job_expiration(self):
        self.take()
        self.corrupt([("UPDATE job_ledger SET expires_at=161", None)])
        with self.assertRaisesRegex(ContractError, 'job expiration'):
            self.check()

    def test_corrupt_stored_artifacts_are_refused_before_any_runner_is_built(self):
        self.take()
        self.execute("UPDATE acceptance_ledger SET result_wire='corrupt'::bytea")
        runner_factory = Mock()
        with self.assertRaisesRegex(ContractError, 'wire digest mismatch'):
            build(root_secret=ROOT_SECRET, policy=self.policy,
                  api_keys={b'a-public-fixture-api-key': 'subject-demo'},
                  workers=(DeterministicSummarizer(),), runner_factory=runner_factory,
                  anchor=AuditAnchor(), acceptance_ledger=self.store)
        runner_factory.assert_not_called()

    def test_expired_historical_artifacts_are_rechecked_at_the_original_acceptance_time(self):
        self.take()
        # Fixtures expired in Unix second 160; today's clock must not turn a
        # valid historical acceptance into corruption during reconstruction.
        self.check(self.ledger())

    def test_changed_trusted_policy_or_keys_refuse_historical_acceptance(self):
        self.take()
        wrong_handoff = type(self.signer)(integrity_key=b'a-different-handoff-key-32bytes!!')
        wrong_worker = type(self.worker_authority)(result_key=b'a-different-worker-key-32bytes!!!')
        arguments = dict(policy=self.policy, handoff_verifier=self.signer.verifier(),
                         worker_verifier=self.worker_authority.verifier())
        for changed in (dict(policy=self.policy_for(worker_agent_id='worker-other')),
                        dict(handoff_verifier=wrong_handoff.verifier()),
                        dict(worker_verifier=wrong_worker.verifier())):
            with self.subTest(changed=tuple(changed)), self.assertRaises(ContractError):
                self.store.check(**{**arguments, **changed})

    def test_start_needs_trusted_policy_and_only_public_verifiers(self):
        arguments = dict(policy=self.policy, handoff_verifier=self.signer.verifier(),
                         worker_verifier=self.worker_authority.verifier())
        for changed, reason in ((dict(policy=object()), 'needs a Policy'),
                                (dict(handoff_verifier=self.signer), 'needs a HandoffVerifier'),
                                (dict(worker_verifier=self.worker_authority), 'needs a WorkerVerifier')):
            with self.subTest(changed=tuple(changed)), self.assertRaisesRegex(ContractError, reason):
                self.store.check(**{**arguments, **changed})


class AcceptanceLedgerInputTest(Fixture, unittest.TestCase):
    def test_the_verifier_refuses_an_unrecognized_ledger(self):
        with self.assertRaisesRegex(ContractError, 'must be an AcceptanceLedger'):
            ResultVerifier(verifier_id='verifier-1', handoff_verifier=self.signer.verifier(),
                           worker_verifier=self.worker_authority.verifier(),
                           acceptance_ledger=object())

    def test_build_refuses_an_unrecognized_ledger(self):
        with self.assertRaisesRegex(ContractError, 'must be an AcceptanceLedger'):
            build(root_secret=ROOT_SECRET, policy=self.policy,
                  api_keys={b'a-public-fixture-api-key': 'subject-demo'},
                  workers=(DeterministicSummarizer(),), anchor=AuditAnchor(),
                  acceptance_ledger=object())

    def test_a_non_psycopg_connection_is_refused(self):
        with self.assertRaisesRegex(ContractError, 'needs a psycopg connection'):
            PostgresAcceptanceLedger(object())

    def test_a_non_autocommit_connection_is_refused(self):
        db = PostgresDatabase()
        self.addCleanup(db.close)
        with psycopg.connect(db.runtime_dsn) as connection:
            with self.assertRaisesRegex(ContractError, 'must be in autocommit mode'):
                PostgresAcceptanceLedger(connection)
