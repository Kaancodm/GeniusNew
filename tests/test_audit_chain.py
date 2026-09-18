import unittest

from geniusnew.audit import AuditAuthority, event_from_handoff
from geniusnew.audit_chain import (AuditAnchor, AuditChain, AuditHead, AuditRecord, sign_head,
                                   verify)
from geniusnew.contracts import ContractError, Grant, Policy, issue, validate


class ChainFixture:
    """Shared setup. Deliberately not a TestCase.

    Subclassing a TestCase to reuse its fixture re-runs every one of its tests
    in the subclass too, which silently doubles the suite and inflates any
    count taken from it.
    """

    def setUp(self):
        self.key = b'phase-2-test-integrity-key-32bytes'
        self.authority = AuditAuthority(audit_key=b'a-separate-audit-key-of-32-bytes!')
        self.other = AuditAuthority(audit_key=b'yet-another-audit-key-of-32bytes!')
        self.actor = self.authority.actor('gateway', 'gw-1')
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
        return event_from_handoff(handoff, trace_id=f'trace-{step}', actor=self.actor,
                                  action='HANDOFF_ADMITTED', decision='ALLOWED',
                                  reason_code='POLICY_SATISFIED', occurred_at=101 + step)


class AuditChainTest(ChainFixture, unittest.TestCase):
    def test_an_intact_chain_verifies_against_its_head(self):
        head = self.chain.head(self.authority)
        self.assertEqual(verify(self.chain.records, head, authority=self.authority), 5)
        self.assertEqual(head.count, 5)
        self.assertEqual(head.head_hash, self.chain.records[-1].record_hash)

    def test_an_empty_chain_verifies(self):
        empty = AuditChain()
        self.assertEqual(verify(empty.records, empty.head(self.authority), authority=self.authority), 0)

    def test_deleting_the_final_record_is_caught(self):
        head = self.chain.head(self.authority)
        truncated = self.chain.records[:-1]
        self.assertEqual(verify(truncated, sign_head(count=4, head_hash=truncated[-1].record_hash,
                                                     authority=self.authority),
                                authority=self.authority), 4)
        with self.assertRaises(ContractError):
            verify(truncated, head, authority=self.authority)

    def test_deleting_a_suffix_is_caught(self):
        head = self.chain.head(self.authority)
        for keep in range(4):
            with self.subTest(keep=keep), self.assertRaises(ContractError):
                verify(self.chain.records[:keep], head, authority=self.authority)

    def test_altering_a_record_is_caught(self):
        head = self.chain.head(self.authority)
        records = list(self.chain.records)
        records[2] = AuditRecord(records[2].index, self.event(99),
                                 records[2].previous_hash, records[2].record_hash)
        with self.assertRaises(ContractError):
            verify(records, head, authority=self.authority)

    def test_inserting_a_record_is_caught(self):
        head = self.chain.head(self.authority)
        records = list(self.chain.records)
        records.insert(2, records[2])
        with self.assertRaises(ContractError):
            verify(records, head, authority=self.authority)

    def test_reordering_records_is_caught(self):
        head = self.chain.head(self.authority)
        records = list(self.chain.records)
        records[1], records[3] = records[3], records[1]
        with self.assertRaises(ContractError):
            verify(records, head, authority=self.authority)

    def test_a_rebuilt_chain_cannot_pass_the_original_head(self):
        """The attack the anchor exists for: shorten the log, relink it cleanly."""
        rebuilt = AuditChain()
        for record in self.chain.records[:3]:
            rebuilt.append(record.event)
        verify(rebuilt.records, rebuilt.head(self.authority), authority=self.authority)
        with self.assertRaises(ContractError):
            verify(rebuilt.records, self.chain.head(self.authority), authority=self.authority)

    def test_a_head_signed_with_another_key_is_refused(self):
        forged = sign_head(count=3, head_hash=self.chain.records[2].record_hash,
                           authority=self.other)
        with self.assertRaises(ContractError):
            verify(self.chain.records[:3], forged, authority=self.authority)

    def test_verification_fails_closed_on_malformed_input(self):
        head = self.chain.head(self.authority)
        for key in (b'too-short', 'not-bytes', None, 42, self.key):
            with self.subTest(key=key), self.assertRaises(ContractError):
                verify(self.chain.records, head, authority=key)
        for bad_head in (None, 'head', 42, head.body()):
            with self.subTest(head=bad_head), self.assertRaises(ContractError):
                verify(self.chain.records, bad_head, authority=self.authority)
        with self.assertRaises(ContractError):
            verify([*self.chain.records, 'not-a-record'], head, authority=self.authority)
        for records in (None, 42, object()):
            with self.subTest(records=type(records)), self.assertRaises(ContractError):
                verify(records, head, authority=self.authority)

    def test_a_tampered_record_fails_as_a_contract_error(self):
        """Malformed stored records must not escape the ContractError boundary."""
        head = self.chain.head(self.authority)
        good = self.chain.records[2]
        for broken in (
            AuditRecord(good.index, good.event, None, good.record_hash),
            AuditRecord(good.index, good.event, good.previous_hash, None),
            AuditRecord(good.index, None, good.previous_hash, good.record_hash),
            AuditRecord('2', good.event, good.previous_hash, good.record_hash),
            AuditRecord(good.index, good.event, 'z' * 64, good.record_hash),
            'not-a-record',
        ):
            records = list(self.chain.records)
            records[2] = broken
            with self.subTest(broken=type(broken)), self.assertRaises(ContractError):
                verify(records, head, authority=self.authority)

    def test_verification_consumes_no_more_than_the_head_allows(self):
        """An endless untrusted iterable must not be materialized."""
        def endless():
            yield from self.chain.records
            while True:
                yield self.chain.records[-1]
        with self.assertRaises(ContractError):
            verify(endless(), self.chain.head(self.authority), authority=self.authority)

    def test_a_head_hash_must_be_a_digest(self):
        for bad in ('z' * 64, 'A' * 64, 'abc', '', None, 42):
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                sign_head(count=1, head_hash=bad, authority=self.authority)
        for bad in (-1, True, '3', None):
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                sign_head(count=bad, head_hash='0' * 64, authority=self.authority)

    def test_concurrent_appends_keep_the_chain_verifiable(self):
        from threading import Barrier, Thread
        chain = AuditChain()
        event = self.event(0)
        barrier = Barrier(8)

        def worker():
            barrier.wait()
            for _ in range(20):
                chain.append(event)

        threads = [Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(chain.records), 160)
        self.assertEqual(verify(chain.records, chain.head(self.authority),
                                authority=self.authority), 160)

    def test_an_anchor_refuses_a_shortened_chain_even_with_a_fresh_head(self):
        """The attack a signed head alone cannot stop: shorten, then re-sign."""
        anchor = AuditAnchor()
        anchor.commit(self.chain.head(self.authority), self.chain.records,
                      authority=self.authority)

        shortened = AuditChain()
        for record in self.chain.records[:3]:
            shortened.append(record.event)
        fresh = shortened.head(self.authority)

        self.assertEqual(verify(shortened.records, fresh, authority=self.authority), 3)
        with self.assertRaises(ContractError):
            verify(shortened.records, fresh, authority=self.authority, anchor=anchor)
        self.assertEqual(verify(self.chain.records, self.chain.head(self.authority),
                                authority=self.authority, anchor=anchor), 5)

    def test_an_anchor_never_moves_backwards(self):
        anchor = AuditAnchor()
        anchor.commit(self.chain.head(self.authority), self.chain.records,
                      authority=self.authority)
        self.assertEqual(anchor.committed[0], 5)
        with self.assertRaises(ContractError):
            anchor.commit(sign_head(count=2, head_hash=self.chain.records[1].record_hash,
                                    authority=self.authority), self.chain.records[:2],
                          authority=self.authority)
        self.assertEqual(anchor.committed[0], 5)
        for bad in (None, 'head', 42):
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                anchor.commit(bad, self.chain.records, authority=self.authority)

    def test_an_anchor_refuses_a_rewrite_at_the_same_length(self):
        """Moving forward is allowed; re-pointing at a rival chain is not."""
        rival = AuditChain()
        for step in range(5):
            rival.append(self.event(step + 90))
        self.assertNotEqual(rival.head(self.authority).head_hash,
                            self.chain.head(self.authority).head_hash)

        anchor = AuditAnchor()
        anchor.commit(self.chain.head(self.authority), self.chain.records,
                      authority=self.authority)
        committed = anchor.committed

        with self.assertRaises(ContractError):
            anchor.commit(rival.head(self.authority), rival.records,
                          authority=self.authority)
        self.assertEqual(anchor.committed, committed)

        # The rival must still be refused by verification after the attempt.
        with self.assertRaises(ContractError):
            verify(rival.records, rival.head(self.authority),
                   authority=self.authority, anchor=anchor)

    def test_committing_the_same_head_twice_is_idempotent(self):
        anchor = AuditAnchor()
        head = self.chain.head(self.authority)
        first = anchor.commit(head, self.chain.records, authority=self.authority)
        self.assertEqual(anchor.commit(head, self.chain.records,
                                       authority=self.authority), first)
        self.assertEqual(anchor.commit(self.chain.head(self.authority),
                                       self.chain.records, authority=self.authority), first)
        self.chain.append(self.event(5))
        self.assertEqual(anchor.commit(self.chain.head(self.authority), self.chain.records,
                                       authority=self.authority)[0], 6)

    def test_an_anchor_catches_a_different_chain_of_the_same_length(self):
        anchor = AuditAnchor()
        anchor.commit(self.chain.head(self.authority), self.chain.records,
                      authority=self.authority)
        rival = AuditChain()
        for step in range(5):
            rival.append(self.event(step + 90))
        with self.assertRaises(ContractError):
            verify(rival.records, rival.head(self.authority),
                   authority=self.authority, anchor=anchor)

    def anchored(self):
        """An anchor holding this test's chain A at five records."""
        anchor = AuditAnchor()
        anchor.commit(self.chain.head(self.authority), self.chain.records,
                      authority=self.authority)
        return anchor

    def rival_chain(self, length):
        """A chain sharing no history with A, of whatever length is asked for."""
        rival = AuditChain()
        for step in range(length):
            rival.append(self.event(step + 90))
        return rival

    def test_a_longer_unrelated_chain_cannot_take_over_the_anchor(self):
        """Being longer is not being an extension.

        A counter that only moves forward accepts any larger number. An
        attacker holding the audit key could therefore fabricate a chain one
        record longer than the anchor, sign it legitimately, and have the
        anchor adopt it — locking the real log out for being too short. The
        commitment is to a chain, so descent has to be shown.
        """
        anchor = self.anchored()
        committed = anchor.committed
        rival = self.rival_chain(6)
        self.assertNotEqual(rival.records[4].record_hash, self.chain.records[4].record_hash)

        with self.assertRaises(ContractError):
            anchor.commit(rival.head(self.authority), rival.records,
                          authority=self.authority)
        self.assertEqual(anchor.committed, committed)

        # The genuine chain must still be the one the anchor accepts.
        self.assertEqual(verify(self.chain.records, self.chain.head(self.authority),
                                authority=self.authority, anchor=anchor), 5)

    def test_a_genuine_extension_is_accepted(self):
        anchor = self.anchored()
        self.chain.append(self.event(5))
        self.assertEqual(anchor.commit(self.chain.head(self.authority), self.chain.records,
                                       authority=self.authority)[0], 6)
        self.assertEqual(anchor.committed[1], self.chain.records[-1].record_hash)
        self.assertEqual(verify(self.chain.records, self.chain.head(self.authority),
                                authority=self.authority, anchor=anchor), 6)

    def test_an_unrelated_chain_stays_refused_at_every_length(self):
        anchor = self.anchored()
        committed = anchor.committed
        for length in range(1, 9):
            rival = self.rival_chain(length)
            with self.subTest(length=length), self.assertRaises(ContractError):
                anchor.commit(rival.head(self.authority), rival.records,
                              authority=self.authority)
        self.assertEqual(anchor.committed, committed)

    def test_verification_refuses_a_longer_chain_that_does_not_contain_the_anchor(self):
        """The same hole on the read path: longer is not descended from."""
        anchor = self.anchored()
        rival = self.rival_chain(6)
        head = rival.head(self.authority)
        self.assertEqual(verify(rival.records, head, authority=self.authority), 6)
        with self.assertRaises(ContractError):
            verify(rival.records, head, authority=self.authority, anchor=anchor)

    def test_a_commit_must_present_the_records_the_head_covers(self):
        anchor = AuditAnchor()
        head = self.chain.head(self.authority)
        for records in (self.chain.records[:4], (), None, 'records',
                        [*self.chain.records, self.chain.records[-1]],
                        [*self.chain.records[:4], 'not-a-record']):
            with self.subTest(records=type(records)), self.assertRaises(ContractError):
                anchor.commit(head, records, authority=self.authority)
        self.assertEqual(anchor.committed, (0, '0' * 64))

    def test_a_first_commit_needs_no_ancestry(self):
        """Nothing is committed yet, so there is nothing to descend from."""
        anchor = AuditAnchor()
        rival = self.rival_chain(3)
        self.assertEqual(anchor.commit(rival.head(self.authority), rival.records,
                                       authority=self.authority)[0], 3)

    def test_a_signed_head_cannot_claim_an_unreadable_count(self):
        """A count is what bounds the read, so it needs a bound of its own.

        `sign_head` accepted any non-negative integer, so a head signed for
        2**64 records verified and then overflowed `islice` — a `ValueError`
        escaping the `ContractError` boundary, reachable by exactly the
        audit-key holder the anchor exists to constrain. Just under that limit
        it was worse than an escape: a licence to read four billion records
        from a hostile iterable.
        """
        for count in (2 ** 32 + 1, 2 ** 63, 10 ** 40, 10 ** 4000):
            with self.subTest(count=count), self.assertRaises(ContractError):
                sign_head(count=count, head_hash=self.chain.records[-1].record_hash,
                          authority=self.authority)

    def test_a_head_claiming_an_unreadable_count_is_refused_not_crashed(self):
        forged = AuditHead('geniusnew-audit-head-v1', 2 ** 70,
                           self.chain.records[-1].record_hash, 'ab' * 32)
        with self.assertRaises(ContractError):
            verify(self.chain.records, forged, authority=self.authority)
        with self.assertRaises(ContractError):
            AuditAnchor().commit(forged, self.chain.records, authority=self.authority)

    def test_a_storage_failure_is_not_reported_as_tampering(self):
        """`ContractError` means the log is untrustworthy, not unreadable."""
        class Unreadable:
            def __iter__(self):
                raise OSError('storage is down')

        with self.assertRaises(OSError):
            verify(Unreadable(), self.chain.head(self.authority), authority=self.authority)

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


class UncoveredRefusalsTest(ChainFixture, unittest.TestCase):
    """One test per refusal `scripts/refusals.py` found nothing covering.

    These are the integrity checks of the chain itself. Every one of them was
    doing its job; the suite simply never exercised it, because the tests that
    look like they should reach it build inputs a different check refuses
    first. Where that is still true, the test asserts the message.
    """

    def signed(self, count, head_hash):
        return sign_head(count=count, head_hash=head_hash, authority=self.authority)

    def test_a_head_claiming_an_unrecognised_version_is_refused(self):
        """A head signed with the audit key but a different version string.

        `sign_head` never writes another version, so this is only reachable by
        signing a body directly — which whoever holds the audit key can do. The
        version is what keeps an audit head from being read as some other
        signed structure, so it has to be checked rather than assumed.
        """
        import hashlib, hmac
        from geniusnew.contracts import canonical
        head_hash = self.chain.records[-1].record_hash
        for version in ('geniusnew-audit-head-v2', 'geniusnew-handoff-v1', 'v1', ''):
            body = {'count': 5, 'head_hash': head_hash, 'version': version}
            signature = hmac.new(self.authority.audit_key, canonical(body),
                                 hashlib.sha256).hexdigest()
            forged = AuditHead(version, 5, head_hash, signature)
            with self.subTest(version=version):
                with self.assertRaisesRegex(ContractError, 'version'):
                    verify(self.chain.records, forged, authority=self.authority)

    def test_a_chain_longer_than_its_head_claims_is_refused(self):
        head = self.signed(3, self.chain.records[2].record_hash)
        with self.assertRaisesRegex(ContractError, 'more than the 3 records'):
            verify(self.chain.records, head, authority=self.authority)

    def test_a_chain_shorter_than_its_head_claims_is_refused(self):
        head = self.signed(5, self.chain.records[-1].record_hash)
        with self.assertRaisesRegex(ContractError, 'holds 4 records'):
            verify(self.chain.records[:4], head, authority=self.authority)

    def test_a_record_claiming_the_wrong_index_is_refused(self):
        """Position is what makes a gap in the middle visible."""
        records = list(self.chain.records)
        good = records[2]
        records[2] = AuditRecord(7, good.event, good.previous_hash, good.record_hash)
        with self.assertRaisesRegex(ContractError, 'claims index 7'):
            verify(records, self.chain.head(self.authority), authority=self.authority)

    def test_a_record_that_does_not_link_to_its_predecessor_is_refused(self):
        """The link itself, with the record's own hash left consistent."""
        records = list(self.chain.records)
        good = records[2]
        broken_previous = 'f' * 64
        records[2] = AuditRecord(good.index, good.event, broken_previous,
                                 good.record_hash)
        with self.assertRaisesRegex(ContractError, 'does not link to its predecessor'):
            verify(records, self.chain.head(self.authority), authority=self.authority)

    def test_a_first_record_not_linked_to_the_empty_hash_is_refused(self):
        records = list(self.chain.records)
        good = records[0]
        records[0] = AuditRecord(good.index, good.event, 'a' * 64, good.record_hash)
        with self.assertRaisesRegex(ContractError, 'record 0 does not link'):
            verify(records, self.chain.head(self.authority), authority=self.authority)

    def test_a_chain_that_ends_elsewhere_than_its_head_says_is_refused(self):
        """Every link intact, the right length, but a different ending.

        Reached by signing a head over a hash that is not this chain's last
        record. The per-record checks all pass, so this final comparison is the
        only thing standing between the log and a head that describes another.
        """
        head = self.signed(5, 'b' * 64)
        with self.assertRaisesRegex(ContractError, 'does not end where'):
            verify(self.chain.records, head, authority=self.authority)

    def test_an_empty_chain_with_a_head_claiming_an_ending_is_refused(self):
        empty = AuditChain()
        head = self.signed(0, 'c' * 64)
        with self.assertRaisesRegex(ContractError, 'does not end where'):
            verify(empty.records, head, authority=self.authority)

    def test_a_record_that_is_not_a_record_is_refused_as_such(self):
        """Asserting the position alone would not have tested this.

        A `str` has an `.index` attribute, so with the type check gone the next
        check refuses it as "non-integer index" — a different message that also
        names the position. Only the type message distinguishes them.
        """
        for position, value in ((0, 'not-a-record'), (2, 42), (4, None),
                                (1, {'index': 1}), (3, b'bytes')):
            records = list(self.chain.records)
            records[position] = value
            with self.subTest(position=position, value=type(value)):
                with self.assertRaisesRegex(
                        ContractError, f'record at position {position} is not an AuditRecord'):
                    verify(records, self.chain.head(self.authority),
                           authority=self.authority)

    def test_a_record_with_a_non_integer_index_is_refused(self):
        records = list(self.chain.records)
        good = records[2]
        for index in ('2', 2.0, None, True):
            records[2] = AuditRecord(index, good.event, good.previous_hash,
                                     good.record_hash)
            with self.subTest(index=repr(index)):
                with self.assertRaisesRegex(ContractError, 'non-integer index'):
                    verify(records, self.chain.head(self.authority),
                           authority=self.authority)

    def test_a_storage_adapter_failing_with_a_type_error_is_not_tampering(self):
        """The distinction the refusal type is supposed to make.

        Catching `TypeError` around `iter()` could not tell "not an iterable"
        from "this iterable's startup raised TypeError" — which is what a
        storage adapter failing to decode does. It was reported as tampering.
        """
        class DecodeFailure:
            def __iter__(self):
                raise TypeError('storage decode failed')

        with self.assertRaises(TypeError):
            verify(DecodeFailure(), self.chain.head(self.authority),
                   authority=self.authority)
        with self.assertRaises(TypeError):
            AuditAnchor().commit(self.chain.head(self.authority), DecodeFailure(),
                                 authority=self.authority)

    def test_something_with_no_iteration_protocol_is_still_refused(self):
        class NotIterable:
            pass

        for records in (NotIterable(), None, 42, object()):
            with self.subTest(records=type(records)):
                with self.assertRaisesRegex(ContractError, 'must be an iterable'):
                    verify(records, self.chain.head(self.authority),
                           authority=self.authority)

    def test_the_record_count_is_bounded_by_what_memory_can_hold(self):
        """Not by what an integer can express.

        The first bound was 2**32. That stopped the `islice` overflow and left
        a signed head able to licence materializing four billion records from a
        hostile iterable — and on a 32-bit build 2**32 is itself past
        `sys.maxsize`, which puts the overflow back.
        """
        import sys as _sys
        from geniusnew.audit_chain import _MAX_COUNT
        self.assertLessEqual(_MAX_COUNT, _sys.maxsize - 1)
        self.assertLessEqual(_MAX_COUNT, 2 ** 31 - 1,
                             'must hold on a 32-bit build too')
        for count in (_MAX_COUNT + 1, 2 ** 32, 2 ** 63):
            with self.subTest(count=count), self.assertRaises(ContractError):
                sign_head(count=count, head_hash='a' * 64, authority=self.authority)

    def test_an_anchor_argument_that_is_not_an_anchor_is_refused(self):
        """Without the guard this reaches `.committed` and escapes as AttributeError."""
        head = self.chain.head(self.authority)
        for anchor in ('anchor', 42, {}, self.chain, (5, 'a' * 64)):
            with self.subTest(anchor=type(anchor)):
                with self.assertRaisesRegex(ContractError, 'must be an AuditAnchor'):
                    verify(self.chain.records, head, authority=self.authority,
                           anchor=anchor)


if __name__ == '__main__':
    unittest.main()
