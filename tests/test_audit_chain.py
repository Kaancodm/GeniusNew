import unittest

from geniusnew.audit import event_from_handoff
from geniusnew.audit_chain import AuditChain, AuditRecord, sign_head, verify
from geniusnew.contracts import ContractError, Grant, Policy, issue, validate


class AuditChainTest(unittest.TestCase):
    def setUp(self):
        self.key = b'phase-2-test-integrity-key-32bytes'
        self.other_key = b'a-different-key-of-at-least-32-bytes'
        grant = Grant('subject-demo', 'user-demo', 'worker-demo', 'basic',
                      ('summarize',), 'isolated', False)
        self.policy = Policy('policy-v1', 'orchestrator-demo', 60,
                             ('summarize',), ('isolated',), (grant,))
        self.chain = AuditChain()
        for step in range(5):
            self.chain.append(self.event(step))

    def event(self, step):
        wire = issue({'text': f'job {step}'}, subject='subject-demo', job_id=f'job-{step}',
                     policy=self.policy, integrity_key=self.key, now=100)
        handoff = validate(wire, subject='subject-demo', job_id=f'job-{step}',
                           policy=self.policy, integrity_key=self.key, now=101)
        return event_from_handoff(handoff, trace_id=f'trace-{step}', actor='gateway',
                                  action='HANDOFF_ADMITTED', decision='ALLOWED',
                                  reason_code='POLICY_SATISFIED', occurred_at=101 + step)

    def test_an_intact_chain_verifies_against_its_head(self):
        head = self.chain.head(self.key)
        self.assertEqual(verify(self.chain.records, head, integrity_key=self.key), 5)
        self.assertEqual(head.count, 5)
        self.assertEqual(head.head_hash, self.chain.records[-1].record_hash)

    def test_an_empty_chain_verifies(self):
        empty = AuditChain()
        self.assertEqual(verify(empty.records, empty.head(self.key), integrity_key=self.key), 0)

    def test_deleting_the_final_record_is_caught(self):
        head = self.chain.head(self.key)
        truncated = self.chain.records[:-1]
        self.assertEqual(verify(truncated, sign_head(count=4, head_hash=truncated[-1].record_hash,
                                                     integrity_key=self.key),
                                integrity_key=self.key), 4)
        with self.assertRaises(ContractError):
            verify(truncated, head, integrity_key=self.key)

    def test_deleting_a_suffix_is_caught(self):
        head = self.chain.head(self.key)
        for keep in range(4):
            with self.subTest(keep=keep), self.assertRaises(ContractError):
                verify(self.chain.records[:keep], head, integrity_key=self.key)

    def test_altering_a_record_is_caught(self):
        head = self.chain.head(self.key)
        records = list(self.chain.records)
        records[2] = AuditRecord(records[2].index, self.event(99),
                                 records[2].previous_hash, records[2].record_hash)
        with self.assertRaises(ContractError):
            verify(records, head, integrity_key=self.key)

    def test_inserting_a_record_is_caught(self):
        head = self.chain.head(self.key)
        records = list(self.chain.records)
        records.insert(2, records[2])
        with self.assertRaises(ContractError):
            verify(records, head, integrity_key=self.key)

    def test_reordering_records_is_caught(self):
        head = self.chain.head(self.key)
        records = list(self.chain.records)
        records[1], records[3] = records[3], records[1]
        with self.assertRaises(ContractError):
            verify(records, head, integrity_key=self.key)

    def test_a_rebuilt_chain_cannot_pass_the_original_head(self):
        """The attack the anchor exists for: shorten the log, relink it cleanly."""
        rebuilt = AuditChain()
        for record in self.chain.records[:3]:
            rebuilt.append(record.event)
        verify(rebuilt.records, rebuilt.head(self.key), integrity_key=self.key)
        with self.assertRaises(ContractError):
            verify(rebuilt.records, self.chain.head(self.key), integrity_key=self.key)

    def test_a_head_signed_with_another_key_is_refused(self):
        forged = sign_head(count=3, head_hash=self.chain.records[2].record_hash,
                           integrity_key=self.other_key)
        with self.assertRaises(ContractError):
            verify(self.chain.records[:3], forged, integrity_key=self.key)

    def test_verification_fails_closed_on_malformed_input(self):
        head = self.chain.head(self.key)
        for key in (b'too-short', 'not-bytes', None, 42):
            with self.subTest(key=key), self.assertRaises(ContractError):
                verify(self.chain.records, head, integrity_key=key)
        for bad_head in (None, 'head', 42, head.body()):
            with self.subTest(head=bad_head), self.assertRaises(ContractError):
                verify(self.chain.records, bad_head, integrity_key=self.key)
        with self.assertRaises(ContractError):
            verify([*self.chain.records, 'not-a-record'], head, integrity_key=self.key)

    def test_the_chain_only_accepts_events(self):
        for value in ('event', 42, None, {'action': 'HANDOFF_ADMITTED'}):
            with self.subTest(value=value), self.assertRaises(ContractError):
                self.chain.append(value)

    def test_appending_links_each_record_to_the_one_before(self):
        records = self.chain.records
        self.assertEqual(records[0].previous_hash, '0' * 64)
        for earlier, later in zip(records, records[1:]):
            self.assertEqual(later.previous_hash, earlier.record_hash)
            self.assertEqual(later.index, earlier.index + 1)


if __name__ == '__main__':
    unittest.main()
