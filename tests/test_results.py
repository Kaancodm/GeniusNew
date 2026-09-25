import unittest

from geniusnew.contracts import ContractError, Grant, HandoffSigner, Policy, issue, validate
from geniusnew.results import (Result, WorkerAuthority, WorkerVerifier, accept,
                               handoff_digest, produce)

# Zero-entropy and self-describing, so no scanner mistakes it for a credential.
# Its only job is to be distinctive enough that finding it somewhere it should
# not be is meaningful.
CANARY = 'OUTPUT-CANARY-MUST-NOT-SURVIVE-A-REFUSAL'

# Distinguishes "the caller left this out" from "the caller passed None", so a
# negative test cannot be handed a valid default in place of the value it means
# to reject.
DEFAULT = object()


class ResultFixture:
    """Shared setup. Deliberately not a TestCase.

    Subclassing a TestCase to reuse its fixture re-runs every one of its tests
    inside the subclass, which silently doubles the suite and inflates any
    count taken from it. Same reason as `test_audit_chain.py`.
    """

    def setUp(self):
        self.key = HandoffSigner(integrity_key=b'phase-2-test-integrity-key-32bytes')
        self.authority = WorkerAuthority(result_key=b'a-separate-result-key-of-32bytes!')
        self.other = WorkerAuthority(result_key=b'yet-another-result-key-32-bytes!!')
        grant = Grant('subject-demo', 'user-demo', 'worker-demo', 'basic',
                      ('summarize',), 'isolated', False)
        self.policy = Policy('policy-v1', 'orchestrator-demo', 60,
                             ('summarize',), ('isolated',), (grant,))
        self.handoff = self.issue_handoff()

    def issue_handoff(self, job_id='job-demo', now=100):
        wire = issue({'text': 'please summarise'}, subject='subject-demo', job_id=job_id,
                     policy=self.policy, signer=self.key, now=now)
        return validate(wire, subject='subject-demo', job_id=job_id,
                        policy=self.policy, verifier=self.key, now=now + 1)

    def wire(self, output=DEFAULT, *, handoff=DEFAULT, status='SUCCEEDED',
             reason_code='WORK_COMPLETED', authority=DEFAULT, now=110):
        """Build a result wire, defaulting only what the caller left out.

        The sentinel matters. An earlier version of this helper defaulted on
        `None` and on falsiness, so every negative test that passed `None`, `{}`
        or `0` silently got a valid value instead and asserted nothing. Four
        tests here passed for that reason before the sentinel replaced it.
        """
        return produce(
            {'text': 'a summary'} if output is DEFAULT else output,
            handoff=self.handoff if handoff is DEFAULT else handoff,
            status=status, reason_code=reason_code,
            authority=self.authority if authority is DEFAULT else authority,
            now=now)

    def taken(self, wire=DEFAULT, *, handoff=DEFAULT, authority=DEFAULT, now=120):
        return accept(
            self.wire() if wire is DEFAULT else wire,
            handoff=self.handoff if handoff is DEFAULT else handoff,
            verifier=self.authority if authority is DEFAULT else authority,
            now=now)


class ResultContractTest(ResultFixture, unittest.TestCase):
    # --- the round trip ----------------------------------------------------

    def test_a_signed_result_is_accepted_and_carries_what_it_claims(self):
        taken = self.taken()
        self.assertIsInstance(taken, Result)
        self.assertTrue(taken.succeeded)
        self.assertEqual(taken.output, {'text': 'a summary'})
        self.assertEqual(taken.job_id, 'job-demo')
        self.assertEqual(taken.worker_agent_id, 'worker-demo')
        self.assertEqual(taken.handoff_sha256, handoff_digest(self.handoff))
        self.assertEqual(taken.reason_code, 'WORK_COMPLETED')
        self.assertEqual(taken.produced_at, 110)

    def test_a_failed_result_is_accepted_without_output(self):
        taken = self.taken(self.wire(None, status='FAILED', reason_code='WORKER_FAILED'))
        self.assertFalse(taken.succeeded)
        self.assertIsNone(taken.output)
        self.assertIsNone(taken.output_sha256)

    # --- the signature -----------------------------------------------------

    def test_a_result_signed_with_another_key_is_refused(self):
        with self.assertRaises(ContractError):
            self.taken(self.wire(authority=self.other))

    def test_the_public_half_accepts_and_cannot_sign(self):
        verifier = self.authority.verifier()
        self.assertTrue(self.taken(authority=verifier).succeeded)
        self.assertFalse(hasattr(verifier, 'sign'))
        self.assertEqual(len(verifier.public_key), 32)
        self.assertNotIn(self.authority.result_key, vars(verifier).values())
        with self.assertRaisesRegex(ContractError, 'authority must be a WorkerAuthority'):
            self.wire(authority=verifier)
        for key in (b'', b'x' * 31, b'x' * 33, 'x' * 32, None):
            with self.subTest(key=repr(key)[:12]), self.assertRaisesRegex(
                    ContractError, 'public_key must be 32 bytes'):
                WorkerVerifier(public_key=key)
        for bad in (None, 'verifier', self.authority.result_key):
            with self.subTest(verifier=type(bad)), self.assertRaisesRegex(
                    ContractError, 'verifier must be a WorkerVerifier'):
                self.taken(authority=bad)

    def test_a_result_signed_with_hmac_is_refused(self):
        """Version 1 was HMAC. A correctly MAC'd body is not a signed one."""
        import hashlib
        import hmac
        import json
        from geniusnew.contracts import canonical
        decoded = json.loads(self.wire())
        body = {k: v for k, v in decoded.items() if k != 'signature'}
        for version in (decoded['version'], 'geniusnew-result-v1'):
            body['version'] = version
            decoded = {**body, 'signature': hmac.new(
                b'a-separate-result-key-of-32bytes!', canonical(body), hashlib.sha256).hexdigest()}
            with self.subTest(version=version), self.assertRaisesRegex(
                    ContractError, 'result signature is invalid'):
                self.taken(canonical(decoded))

    def test_a_malformed_signature_is_refused_before_it_is_decoded(self):
        import json
        from geniusnew.contracts import canonical
        decoded = json.loads(self.wire())
        good = decoded['signature']
        for signature in ('0' * 64, 'zz' * 64, good.upper(), good[:-2], good + '00'):
            with self.subTest(signature=signature[:8]):
                decoded['signature'] = signature
                with self.assertRaisesRegex(ContractError, 'result signature is invalid'):
                    self.taken(canonical(decoded))

    def test_the_handoff_key_cannot_be_used_as_a_result_key(self):
        """Signing results with the handoff key would let a worker authorize itself."""
        padded = b'phase-2-test-integrity-key-32bytes-padding-to-thirty-two-bytes'
        with self.assertRaises(ContractError):
            WorkerAuthority(result_key=padded, integrity_key=padded)
        WorkerAuthority(result_key=padded, integrity_key=b'a-different-key-of-32-bytes-long!')
        for bad in (b'too-short', 'not-bytes', None, 32):
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                WorkerAuthority(result_key=bad)

    def test_altering_any_signed_field_is_caught(self):
        import json
        original = self.wire()
        decoded = json.loads(original)
        replacements = {
            'version': 'geniusnew-result-v3',
            'job_id': 'job-other',
            'worker_agent_id': 'worker-other',
            'handoff_sha256': 'a' * 64,
            'status': 'FAILED',
            'reason_code': 'TAMPERED',
            'output': {'text': 'a different summary'},
            'output_sha256': 'b' * 64,
            'produced_at': 111,
        }
        for field, replacement in replacements.items():
            tampered = dict(decoded)
            tampered[field] = replacement
            wire = json.dumps(tampered, ensure_ascii=True, sort_keys=True,
                              separators=(',', ':')).encode('ascii')
            with self.subTest(field=field), self.assertRaises(ContractError):
                self.taken(wire)

    # --- binding to the handoff -------------------------------------------

    def test_a_result_for_another_job_is_refused(self):
        """The replay this binding exists for: lift a result, answer a different job."""
        other_handoff = self.issue_handoff(job_id='job-other')
        self.assertNotEqual(handoff_digest(other_handoff), handoff_digest(self.handoff))
        for produced_for, accepted_against in ((self.handoff, other_handoff),
                                               (other_handoff, self.handoff)):
            with self.subTest(job=accepted_against.job_id), self.assertRaises(ContractError):
                self.taken(self.wire(handoff=produced_for), handoff=accepted_against)

    def test_a_result_is_bound_to_the_exact_handoff_not_just_its_job_id(self):
        """Same job id, different artifact — the digest still has to match."""
        reissued = self.issue_handoff(now=101)
        self.assertEqual(reissued.job_id, self.handoff.job_id)
        self.assertNotEqual(handoff_digest(reissued), handoff_digest(self.handoff))
        with self.assertRaises(ContractError):
            self.taken(self.wire(handoff=self.handoff), handoff=reissued)

    def test_the_handoff_digest_is_the_one_the_audit_entry_uses(self):
        import hashlib
        self.assertEqual(handoff_digest(self.handoff),
                         hashlib.sha256(self.handoff.to_bytes()).hexdigest())

    # --- a failure must not smuggle output ---------------------------------

    def test_a_failed_result_cannot_carry_output(self):
        with self.assertRaises(ContractError):
            self.wire({'text': CANARY}, status='FAILED', reason_code='WORKER_FAILED')

    def test_a_forged_failure_carrying_output_is_refused(self):
        """The exfiltration shape: return data under a status that skips output checks."""
        import json
        decoded = json.loads(self.wire(None, status='FAILED', reason_code='WORKER_FAILED'))
        for smuggled in ({'text': CANARY}, 'raw text', {'text': CANARY, 'extra': 1}):
            tampered = {**decoded, 'output': smuggled}
            signed = {k: v for k, v in tampered.items() if k != 'signature'}
            tampered['signature'] = self.authority.sign(signed)
            wire = json.dumps(tampered, ensure_ascii=True, sort_keys=True,
                              separators=(',', ':')).encode('ascii')
            with self.subTest(smuggled=type(smuggled)), self.assertRaises(ContractError):
                self.taken(wire)

    def test_a_success_must_carry_output_that_matches_its_digest(self):
        import hashlib, json
        decoded = json.loads(self.wire())
        for output in ({'text': 'swapped'}, None, {'text': ''}, {'text': 1}, 'raw'):
            tampered = {**decoded, 'output': output}
            signed = {k: v for k, v in tampered.items() if k != 'signature'}
            tampered['signature'] = self.authority.sign(signed)
            wire = json.dumps(tampered, ensure_ascii=True, sort_keys=True,
                              separators=(',', ':')).encode('ascii')
            with self.subTest(output=output), self.assertRaises(ContractError):
                self.taken(wire)

    # --- the closed sets ---------------------------------------------------

    def test_status_comes_from_a_closed_set(self):
        for status in ('SUCCESS', 'succeeded', 'PARTIAL', '', None, 1, []):
            with self.subTest(status=status), self.assertRaises(ContractError):
                self.wire(status=status)

    def test_a_reason_code_must_come_from_the_closed_set(self):
        """A shape is not a closed set.

        `[A-Z][A-Z0-9_]{0,63}` refused prose and accepted sixty-three
        characters of base32 — which under `FAILED`, where no output check
        applies, is the exfiltration path the empty-output rule exists to
        close, reopened one field along.
        """
        for code in (f'FAILED {CANARY}', CANARY, 'lowercase', 'HAS SPACE',
                     'TRAILING-DASH', 'A' * 65, '', 1, None,
                     'SECRETDATA0123456789ABCDEFGHIJKLMNOP',  # the smuggling case
                     'WORK_COMPLETED_', 'WORKCOMPLETED', 'UNKNOWN_CODE'):
            with self.subTest(code=code), self.assertRaises(ContractError):
                self.wire(reason_code=code)

    def test_every_allowed_reason_code_round_trips(self):
        from geniusnew.results import _REASON_CODES
        for code in sorted(_REASON_CODES):
            with self.subTest(code=code):
                status = 'SUCCEEDED' if code == 'WORK_COMPLETED' else 'FAILED'
                output = {'text': 'a summary'} if status == 'SUCCEEDED' else None
                wire = self.wire(output, status=status, reason_code=code)
                self.assertEqual(self.taken(wire).reason_code, code)

    def test_output_is_held_to_the_same_rule_as_a_handoff_payload(self):
        """Symmetry as a check: the two accept and refuse the same shapes."""
        shapes = ({'text': 'ok'}, {'text': ''}, {'text': 1}, {'text': None},
                  {'text': 'ok', 'extra': 'no'}, {}, {'other': 'x'}, 'raw', 42, None, [])
        for shape in shapes:
            handoff_ok = result_ok = True
            try:
                issue(shape, subject='subject-demo', job_id='j', policy=self.policy,
                      signer=self.key, now=100)
            except ContractError:
                handoff_ok = False
            try:
                self.wire(shape)
            except ContractError:
                result_ok = False
            with self.subTest(shape=shape):
                self.assertEqual(handoff_ok, result_ok)

    # --- time --------------------------------------------------------------

    def test_a_result_cannot_predate_the_handoff_it_answers(self):
        with self.assertRaises(ContractError):
            self.taken(self.wire(now=self.handoff.issued_at - 1))

    def test_a_result_dated_in_the_future_is_refused(self):
        with self.assertRaises(ContractError):
            self.taken(self.wire(now=130), now=129)

    def test_a_result_produced_after_its_handoff_expired_is_refused(self):
        expired_at = self.handoff.expires_at
        with self.assertRaises(ContractError):
            self.taken(self.wire(now=expired_at), now=expired_at)

    def test_a_handoff_that_expires_during_execution_loses_its_result(self):
        """Roadmap step 15's fourth control point, at the acceptance boundary.

        The result was produced while the handoff was still valid. Acceptance
        happens after it expired, and a resource time limit would not catch
        this — only a check against the handoff's own window does.
        """
        in_time = self.wire(now=self.handoff.expires_at - 1)
        self.assertTrue(self.taken(in_time, now=self.handoff.expires_at - 1).succeeded)
        with self.assertRaises(ContractError):
            self.taken(in_time, now=self.handoff.expires_at)

    def test_the_time_reference_fails_closed(self):
        for now in (True, 0, -1, '110', 110.0, None):
            with self.subTest(now=now), self.assertRaises(ContractError):
                self.wire(now=now)

    # --- the wire ----------------------------------------------------------

    def test_the_wire_refusals_apply_to_results_too(self):
        import json
        original = self.wire()
        decoded = json.loads(original)
        for broken in (
            b'', b'{', b'null', b'[]', b'"text"', b'{}',
            original + b' ',
            json.dumps(decoded, indent=2).encode(),                      # not canonical
            json.dumps({**decoded, 'extra': 1}, sort_keys=True,
                       separators=(',', ':')).encode(),                  # extra key
            json.dumps({k: v for k, v in decoded.items() if k != 'status'},
                       sort_keys=True, separators=(',', ':')).encode(),  # missing key
            b'{"version":"x","version":"y"}',                            # duplicate keys
            original.decode(),                                           # not bytes
            None, 42, [],
        ):
            with self.subTest(broken=repr(broken)[:40]), self.assertRaises(ContractError):
                self.taken(broken)

    def test_produce_cannot_emit_a_result_accept_would_refuse(self):
        """The bug 2525475 fixed for `issue`, kept out of `produce` by construction."""
        with self.assertRaises(ContractError):
            self.wire({'text': 'x' * (17 * 1024)})

    def test_a_result_at_the_size_limit_still_round_trips(self):
        wire = self.wire({'text': 'x' * 15000})
        self.assertLessEqual(len(wire), 16 * 1024)
        self.assertTrue(self.taken(wire).succeeded)

    # --- properties worth pinning ------------------------------------------

    def test_an_accepted_result_serializes_back_to_the_exact_wire(self):
        wire = self.wire()
        self.assertEqual(self.taken(wire).to_bytes(), wire)

    def test_a_handoff_mutated_after_validation_loses_its_result(self):
        """`Handoff.payload` is still mutable, so the binding has to notice."""
        handoff = self.issue_handoff()
        wire = self.wire(handoff=handoff)
        handoff.payload['text'] = 'swapped after validation'
        with self.assertRaises(ContractError):
            self.taken(wire, handoff=handoff)

    def test_a_handoff_mutated_before_producing_cannot_be_answered(self):
        """The case mutating afterwards hides.

        Mutate before `produce` and both sides hash the same mutated object, so
        the digests agree and the result binds to a payload that was never
        signed. Only comparing against `payload_sha256` — which is inside the
        signed body and does not move — catches it.
        """
        handoff = self.issue_handoff()
        handoff.payload['text'] = 'mutated before produce, never signed'
        with self.assertRaisesRegex(ContractError, 'no longer matches the digest'):
            self.wire(handoff=handoff)
        with self.assertRaises(ContractError):
            handoff_digest(handoff)

    def test_the_same_result_can_be_accepted_twice(self):
        """A stated limitation, pinned so it cannot be mistaken for a guarantee.

        A contract holds no state, so it cannot make acceptance single-use. The
        result is bound to its handoff, but replaying the same result is the
        accepting instance's problem, the way `approvals.py` solves it for
        approvals. Roadmap step 14 owns that.
        """
        wire = self.wire()
        self.assertTrue(self.taken(wire).succeeded)
        self.assertTrue(self.taken(wire, now=121).succeeded)

    # --- fail closed -------------------------------------------------------

    def test_every_entry_point_fails_closed_on_malformed_input(self):
        for handoff in (None, 'handoff', 42, {}, self.handoff.to_bytes()):
            with self.subTest(handoff=type(handoff)), self.assertRaises(ContractError):
                self.wire(handoff=handoff)
            with self.subTest(handoff=type(handoff)), self.assertRaises(ContractError):
                self.taken(handoff=handoff)
            with self.subTest(handoff=type(handoff)), self.assertRaises(ContractError):
                handoff_digest(handoff)
        for authority in (None, 'authority', 42, self.authority.result_key):
            with self.subTest(authority=type(authority)), self.assertRaises(ContractError):
                self.wire(authority=authority)
            with self.subTest(authority=type(authority)), self.assertRaises(ContractError):
                self.taken(authority=authority)

    def test_hostile_input_never_escapes_the_contract_error_boundary(self):
        class Evil:
            def __eq__(self, other): raise RuntimeError('evil __eq__')
            def __hash__(self): raise RuntimeError('evil __hash__')
            def __len__(self): raise RuntimeError('evil __len__')

        recursive = {}
        recursive['self'] = recursive
        hostile = [None, 0, -1, True, 1.5, float('nan'), 10 ** 4000, '', 'x' * 100000,
                   b'', [], (), {}, set(), {1: 2}, recursive, Evil(), object(), type]
        for value in hostile:
            for call in (
                lambda v=value: self.wire(v),
                lambda v=value: self.wire(status=v),
                lambda v=value: self.wire(reason_code=v),
                lambda v=value: self.wire(now=v),
                lambda v=value: self.taken(v),
            ):
                with self.subTest(value=type(value)):
                    try:
                        call()
                    except ContractError:
                        pass
                    except Exception as exc:  # noqa: BLE001 - that is the point
                        self.fail(f'{type(exc).__name__} escaped ContractError: {exc}')


class UncoveredRefusalsTest(ResultFixture, unittest.TestCase):
    """One test per refusal `scripts/refusals.py` found nothing covering.

    Most of these survived because the same condition is enforced on both
    sides: delete it from `produce` and `accept` still refuses, delete it from
    `accept` and `produce` refuses first. That redundancy is deliberate, but it
    meant neither side was individually tested — so these exercise each half on
    its own, and re-sign the wire where a forged field would otherwise be
    caught by the signature check before the field's own check runs.
    """

    def resigned(self, **over):
        """A wire with fields replaced and the signature recomputed over them."""
        import json
        decoded = {**json.loads(self.wire()), **over}
        body = {k: v for k, v in decoded.items() if k != 'signature'}
        decoded['signature'] = self.authority.sign(body)
        return json.dumps(decoded, ensure_ascii=True, sort_keys=True,
                          separators=(',', ':')).encode('ascii')

    # --- each side of the duplicated time bounds, on its own -----------------

    def test_produce_itself_refuses_a_time_outside_the_handoff_window(self):
        """Not the round trip: `produce` must refuse before it signs anything."""
        for now in (0, -1, self.handoff.issued_at - 1, self.handoff.expires_at,
                    self.handoff.expires_at + 1, 4102444801):
            with self.subTest(now=now), self.assertRaises(ContractError):
                self.wire(now=now)

    def test_accept_refuses_a_produced_at_outside_the_window_on_its_own(self):
        """A correctly signed wire that `produce` would never have emitted."""
        # `now` is chosen per case so the intended check is the one that fires:
        # the future check sits before the expiry check, and would otherwise
        # swallow every produced_at past the window.
        cases = [
            (0, 125, 'between 1 and'),
            (-1, 125, 'between 1 and'),
            (4102444801, 4102444900, 'between 1 and'),
            (self.handoff.issued_at - 1, 125, 'predates'),
            (self.handoff.expires_at, self.handoff.expires_at, 'after its handoff expired'),
            (self.handoff.expires_at + 5, self.handoff.expires_at + 5,
             'after its handoff expired'),
        ]
        for produced_at, now, expected in cases:
            with self.subTest(produced_at=produced_at):
                with self.assertRaisesRegex(ContractError, expected):
                    self.taken(self.resigned(produced_at=produced_at), now=now)

    def test_accept_refuses_a_result_dated_after_now_on_its_own(self):
        with self.assertRaisesRegex(ContractError, 'future'):
            self.taken(self.resigned(produced_at=119), now=118)

    # --- fields whose own check sits behind the signature check --------------

    def test_a_correctly_signed_wire_with_a_wrong_version_is_refused(self):
        for version in ('geniusnew-result-v1', 'geniusnew-result-v3', 'geniusnew-handoff-v2', 'x'):
            with self.subTest(version=version):
                with self.assertRaisesRegex(ContractError, 'version'):
                    self.taken(self.resigned(version=version))

    def test_a_correctly_signed_wire_with_an_unknown_status_is_refused(self):
        for status in ('SUCCESS', 'succeeded', 'PARTIAL', 'PENDING'):
            with self.subTest(status=status):
                with self.assertRaisesRegex(ContractError, 'status'):
                    self.taken(self.resigned(status=status))

    def test_a_correctly_signed_wire_with_an_empty_identifier_is_refused(self):
        # `signature` is excluded: the helper recomputes it, so replacing it
        # would be overwritten and the test would assert nothing.
        for field in ('version', 'job_id', 'worker_agent_id', 'status',
                      'reason_code'):
            for value in ('', None, 42):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ContractError):
                        self.taken(self.resigned(**{field: value}))

    def test_a_correctly_signed_wire_with_a_malformed_digest_is_refused(self):
        for digest in ('z' * 64, 'A' * 64, 'abc', 'a' * 63, '', None, 42):
            with self.subTest(digest=repr(digest)[:12]):
                with self.assertRaises(ContractError):
                    self.taken(self.resigned(handoff_sha256=digest))

    # --- the authority guard -------------------------------------------------

    def test_produce_bounds_the_time_even_inside_the_handoff_window(self):
        """The one case the handoff window cannot cover.

        `produce` checks the absolute bound and then the handoff's own window,
        and for an ordinary handoff the window refuses everything the bound
        would. It is not redundant, because `contracts.py` lets `issue` stamp a
        handoff at a non-positive time: the window around it then legitimately
        contains times the bound must still refuse.

        That `issue` accepts `now <= 0` at all looks like a gap in the handoff
        contract rather than a feature, but fixing it belongs to that module.
        """
        wire = issue({'text': 'before the epoch'}, subject='subject-demo',
                     job_id='job-demo', policy=self.policy, signer=self.key, now=-5)
        handoff = validate(wire, subject='subject-demo', job_id='job-demo',
                           policy=self.policy, verifier=self.key, now=-4)
        self.assertLess(handoff.issued_at, 0)
        for now in (-4, -1, 0):
            self.assertTrue(handoff.issued_at <= now < handoff.expires_at,
                            'the case only holds inside the handoff window')
            with self.subTest(now=now):
                with self.assertRaisesRegex(ContractError, 'between 1 and'):
                    self.wire(handoff=handoff, now=now)

    def test_a_malformed_signature_is_refused(self):
        import json
        for signature in ('', None, 42, 'z' * 64, 'a' * 63):
            decoded = {**json.loads(self.wire()), 'signature': signature}
            wire = json.dumps(decoded, ensure_ascii=True, sort_keys=True,
                              separators=(',', ':')).encode('ascii')
            with self.subTest(signature=repr(signature)[:12]), self.assertRaises(ContractError):
                self.taken(wire)

    def test_an_integrity_key_that_is_not_bytes_is_refused(self):
        """It cannot be compared against, so it cannot be ruled out — refuse it."""
        for integrity_key in ('not-bytes', 42, [], {}, object()):
            with self.subTest(integrity_key=type(integrity_key)):
                with self.assertRaises(ContractError):
                    WorkerAuthority(result_key=b'a-separate-result-key-of-32bytes!',
                                    integrity_key=integrity_key)


if __name__ == '__main__':
    unittest.main()
