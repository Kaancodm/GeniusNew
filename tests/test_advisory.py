"""The advisory boundary: text out, no authority in, every edge refused.

The engine here is a test double on purpose. What this file proves is the
boundary in `geniusnew/advisory.py`; the pilot beside it is proven separately
against a fake Ollama in `tests/test_ai_pilot.py`. Each refusal below names the
check that must fail the suite when `scripts/refusals.py` removes it, which is
why there is one test per refusal rather than one test per method.
"""

import sys
import threading
import traceback
import unittest
from dataclasses import fields
from unittest import mock

from geniusnew.advisory import (ADVISORY_KINDS, MAX_ADVISORY_INPUT_BYTES,
                                MAX_ADVISORY_TEXT_BYTES, AdvisoryBridge,
                                AdvisoryResult, AdvisorySettings, build_advisory,
                                default_engine)
from geniusnew.audit_chain import AuditAnchor
from geniusnew.contracts import ContractError, Grant, Policy
from geniusnew.keys import derive_keys
from geniusnew.results import WorkerAuthority
from geniusnew.wiring import build
from geniusnew.workers import DeterministicSummarizer, WorkerRunner
from tools.ai_pilot.pilot import Pilot

ROOT_SECRET = b'NOT-A-SECRET-' + b'0' * 24
API_KEY = b'API-KEY-CANARY-MUST-NOT-BE-DISCLOSED'
ANSWER = 'Two harmless sentences about a verified chain.'


def assert_private_refusal(test, call, message, canary):
    # Capture the live traceback: assertRaises clears it when saving the error.
    try:
        call()
    except ContractError as exc:
        test.assertEqual(str(exc), message)
        test.assertIsNone(exc.__context__)
        test.assertIsNone(exc.__cause__)
        test.assertNotIn(canary, ''.join(traceback.format_exception(exc)))
    else:
        test.fail('expected a private ContractError')


class Engine:
    """Scripts one advisory call and records what the boundary handed over."""

    def __init__(self, answer=ANSWER, raises=None):
        self.calls = []
        self.answer = answer
        self.raises = raises

    def run(self, kind, text):
        self.calls.append((kind, text))
        if self.raises is not None:
            raise self.raises
        return self.answer


class BlockingEngine:
    """Holds one call open so a second caller can be observed."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def run(self, kind, text):
        self.calls += 1
        self.entered.set()
        self.release.wait(5)
        return ANSWER


class StubRunner(WorkerRunner):
    """Constructed, never executed: these tests ask the wiring, not a job."""

    def execute(self, permit, *, now):  # pragma: no cover - must not run
        raise AssertionError('no job runs in the advisory tests')


class BridgeTest(unittest.TestCase):
    def bridge(self, *, enabled=True, **engine_options):
        engine = Engine(**engine_options)
        return AdvisoryBridge(engine=engine, enabled=enabled), engine

    def refuse(self, bridge, *, kind='summary', text='A harmless note.'):
        with self.assertRaises(ContractError) as raised:
            bridge.request(kind, text)
        return str(raised.exception)

    def test_a_disabled_bridge_refuses_before_the_engine_is_asked(self):
        bridge, engine = self.bridge(enabled=False)
        self.assertIn('disabled', self.refuse(bridge))
        self.assertEqual(engine.calls, [])

    def test_a_non_boolean_enable_flag_stays_disabled(self):
        bridge, engine = self.bridge(enabled='yes')  # type: ignore[arg-type]
        self.assertFalse(bridge.enabled)
        self.assertIn('disabled', self.refuse(bridge))
        self.assertEqual(engine.calls, [])

    def test_an_enabled_bridge_without_an_engine_is_refused(self):
        with self.assertRaises(ContractError) as raised:
            AdvisoryBridge(enabled=True)
        self.assertIn('needs an engine', str(raised.exception))

    def test_an_engine_without_run_is_refused(self):
        with self.assertRaises(ContractError) as raised:
            AdvisoryBridge(engine=object(), enabled=True)
        self.assertIn('run(kind, text)', str(raised.exception))

    def test_a_known_task_returns_the_text_and_its_digest(self):
        bridge, engine = self.bridge()
        result = bridge.request('code_draft', 'Draft a function.')
        self.assertEqual((result.kind, result.text), ('code_draft', ANSWER))
        self.assertEqual(engine.calls, [('code_draft', 'Draft a function.')])
        self.assertEqual(len(result.text_sha256), 64)

    def test_the_result_carries_nothing_but_kind_text_and_digest(self):
        bridge, _ = self.bridge()
        result = bridge.request('summary', 'A harmless note.')
        self.assertIsInstance(result, AdvisoryResult)
        self.assertEqual(sorted(item.name for item in fields(result)),
                         ['kind', 'text', 'text_sha256'])

    def test_an_unknown_kind_is_refused_before_the_engine_is_asked(self):
        bridge, engine = self.bridge()
        self.assertIn('kind must be one of', self.refuse(bridge, kind='shell'))
        self.assertEqual(engine.calls, [])

    def test_a_non_string_kind_is_refused(self):
        bridge, _ = self.bridge()
        self.assertIn('kind must be one of', self.refuse(bridge, kind=7))

    def test_an_empty_or_blank_input_is_refused(self):
        bridge, engine = self.bridge()
        for text in ('', '   \n\t'):
            self.assertIn('non-empty text', self.refuse(bridge, text=text))
        self.assertEqual(engine.calls, [])

    def test_input_with_nul_is_refused(self):
        bridge, _ = self.bridge()
        self.assertIn('without NUL', self.refuse(bridge, text='a\x00b'))

    def test_an_oversize_input_is_refused(self):
        bridge, engine = self.bridge()
        text = 'x' * (MAX_ADVISORY_INPUT_BYTES + 1)
        self.assertIn('at most', self.refuse(bridge, text=text))
        self.assertEqual(engine.calls, [])

    def test_unencodable_input_is_refused(self):
        canary = 'INPUT-CANARY-MUST-NOT-BE-DISCLOSED'
        bridge, engine = self.bridge()
        assert_private_refusal(
            self, lambda: bridge.request('summary', canary + '\ud800'),
            'advisory input for summary must be encodable as UTF-8', canary)
        self.assertEqual(engine.calls, [])
        self.assertEqual(bridge.request('summary', 'A harmless note.').text, ANSWER)

    def test_a_second_concurrent_request_is_refused(self):
        engine = BlockingEngine()
        bridge = AdvisoryBridge(engine=engine, enabled=True)
        first = threading.Thread(target=lambda: bridge.request('summary', 'first'),
                                 daemon=True)
        first.start()
        self.addCleanup(first.join, 5)
        self.assertTrue(engine.entered.wait(5))
        self.assertIn('holds this bridge', self.refuse(bridge, text='second'))
        engine.release.set()
        self.assertEqual(engine.calls, 1)

    def test_an_engine_exception_becomes_one_refusal_without_its_message(self):
        canary = 'ENGINE-INTERNAL-CANARY-MUST-NOT-BE-DISCLOSED'
        for error_type in (RuntimeError, ValueError, ContractError):
            with self.subTest(error=error_type.__name__):
                bridge, engine = self.bridge(raises=error_type(canary))
                assert_private_refusal(
                    self, lambda: bridge.request('summary', 'A harmless note.'),
                    'the advisory engine failed', canary)
                engine.raises = None
                self.assertEqual(bridge.request('summary', 'After failure.').text,
                                 ANSWER)

    def test_engine_base_exceptions_are_private_refusals_and_release_the_lock(self):
        class EngineFailure(BaseException):
            pass

        canary = 'ENGINE-BASE-CANARY-MUST-NOT-BE-DISCLOSED'
        for error_type in (SystemExit, GeneratorExit, EngineFailure):
            with self.subTest(error=error_type.__name__):
                bridge, engine = self.bridge(raises=error_type(canary))
                assert_private_refusal(
                    self, lambda: bridge.request('summary', 'A harmless note.'),
                    'the advisory engine failed', canary)
                engine.raises = None
                self.assertEqual(bridge.request('summary', 'After failure.').text,
                                 ANSWER)

    def test_keyboard_interrupt_propagates_and_releases_the_lock(self):
        interruption = KeyboardInterrupt()
        bridge, engine = self.bridge(raises=interruption)
        with self.assertRaises(KeyboardInterrupt) as raised:
            bridge.request('summary', 'A harmless note.')
        self.assertIs(raised.exception, interruption)
        engine.raises = None
        self.assertEqual(bridge.request('summary', 'After interruption.').text,
                         ANSWER)

    def test_a_non_string_answer_is_refused(self):
        bridge, _ = self.bridge(answer={'text': ANSWER})
        self.assertIn('must be text', self.refuse(bridge))

    def test_an_empty_answer_is_refused(self):
        bridge, _ = self.bridge(answer='  \n')
        self.assertIn('non-empty text', self.refuse(bridge))

    def test_an_answer_with_nul_is_refused(self):
        bridge, _ = self.bridge(answer=ANSWER + '\x00')
        self.assertIn('without NUL', self.refuse(bridge))

    def test_an_oversize_answer_is_refused(self):
        bridge, _ = self.bridge(answer='y' * (MAX_ADVISORY_TEXT_BYTES + 1))
        self.assertIn('at most', self.refuse(bridge))

    def test_unencodable_answer_is_refused(self):
        canary = 'OUTPUT-CANARY-MUST-NOT-BE-DISCLOSED'
        bridge, engine = self.bridge(answer=canary + '\udfff')
        assert_private_refusal(
            self, lambda: bridge.request('summary', 'A harmless note.'),
            'advisory output must be encodable as UTF-8', canary)
        engine.answer = ANSWER
        self.assertEqual(bridge.request('summary', 'After failure.').text, ANSWER)


class SettingsTest(unittest.TestCase):
    def test_settings_refuse_a_non_boolean_enabled(self):
        with self.assertRaises(ContractError) as raised:
            AdvisorySettings(enabled='yes')  # type: ignore[arg-type]
        self.assertIn('must be a boolean', str(raised.exception))

    def test_a_disabled_setting_builds_a_bridge_without_importing_the_engine(self):
        with mock.patch('geniusnew.advisory.default_engine',
                        side_effect=AssertionError('imported an engine')):
            bridge = build_advisory(AdvisorySettings())
        self.assertFalse(bridge.enabled)
        with self.assertRaises(ContractError) as raised:
            bridge.request('summary', 'note')
        self.assertIn('disabled', str(raised.exception))

    def test_an_enabled_setting_builds_a_bridge_over_the_given_engine(self):
        engine = Engine()
        with mock.patch('geniusnew.advisory.default_engine', return_value=engine):
            bridge = build_advisory(AdvisorySettings(enabled=True))
        self.assertTrue(bridge.enabled)
        self.assertEqual(bridge.request('summary', 'A harmless note.').text, ANSWER)

    def test_no_settings_build_a_disabled_bridge(self):
        self.assertFalse(build_advisory().enabled)

    def test_foreign_settings_are_refused(self):
        with self.assertRaises(ContractError) as raised:
            build_advisory({'enabled': True})  # type: ignore[arg-type]
        self.assertIn('AdvisorySettings', str(raised.exception))

    def test_the_default_engine_is_the_opt_in_pilot(self):
        engine = default_engine()
        self.assertIsInstance(engine, Pilot)
        # The bridge's configuration is the outer gate; the pilot keeps its own.
        self.assertTrue(engine.enabled)

    def test_a_missing_pilot_refuses_the_enabled_setting(self):
        with mock.patch.dict(sys.modules, {'tools.ai_pilot.pilot': None}):
            with self.assertRaises(ContractError) as raised:
                default_engine()
        self.assertIn('not importable', str(raised.exception))

    def test_an_import_error_does_not_disclose_its_context_or_cause(self):
        canary = 'IMPORT-CANARY-MUST-NOT-BE-DISCLOSED'
        original_import = __import__

        def import_pilot(name, *args, **kwargs):
            if name == 'tools.ai_pilot.pilot':
                raise ImportError(canary)
            return original_import(name, *args, **kwargs)

        with mock.patch('builtins.__import__', side_effect=import_pilot):
            assert_private_refusal(
                self, default_engine,
                'the advisory engine tools/ai_pilot is not importable', canary)


class WiringTest(unittest.TestCase):
    def service(self, **options):
        keys = derive_keys(ROOT_SECRET)
        grant = Grant('subject-demo', 'user-demo', 'worker-demo', 'basic',
                      ('summarize',), 'isolated', False)
        policy = Policy('policy-v1', 'orchestrator-1', 60,
                        ('summarize',), ('isolated',), (grant,))
        settings = dict(
            root_secret=ROOT_SECRET, policy=policy,
            api_keys={API_KEY: 'subject-demo'},
            workers=(DeterministicSummarizer(),),
            clock=lambda: 1_700_000_000, anchor=AuditAnchor(),
            runner_factory=lambda worker: StubRunner(
                worker, authority=WorkerAuthority(result_key=keys.result_key,
                                                  integrity_key=keys.integrity_key)))
        settings.update(options)
        service = build(**settings)
        self.addCleanup(service.close)
        return service

    def test_a_service_carries_a_disabled_bridge_by_default(self):
        service = self.service()
        self.assertIsInstance(service.advisory, AdvisoryBridge)
        self.assertFalse(service.advisory.enabled)
        with self.assertRaises(ContractError):
            service.advisory.request('summary', 'A harmless note.')

    def test_a_service_accepts_an_enabled_bridge(self):
        engine = Engine()
        service = self.service(advisory=AdvisoryBridge(engine=engine, enabled=True))
        self.assertEqual(service.advisory.request('summary', 'A harmless note.').text,
                         ANSWER)
        self.assertEqual(engine.calls, [('summary', 'A harmless note.')])

    def test_a_foreign_advisory_is_refused(self):
        with self.assertRaises(ContractError) as raised:
            self.service(advisory=object())
        self.assertIn('AdvisoryBridge', str(raised.exception))

    def test_the_advisory_kinds_are_closed(self):
        self.assertEqual(set(ADVISORY_KINDS), {'summary', 'code_draft'})
