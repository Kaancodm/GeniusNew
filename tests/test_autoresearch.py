"""The keep-or-discard loop: the gates are the script's, not the agent's."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import autoresearch
from scripts.autoresearch import GateFailed, Loop, Measurement

BASE = Measurement(seconds=10.0, tests=100, refusals=20, attacks=13)


class FakeMeasure:
    """Hands back queued measurements instead of running a suite."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, root, target):
        self.calls.append(target)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class LoopTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for command in (['init', '-q', '-b', 'main'],
                        ['config', 'user.email', 'test@example.invalid'],
                        ['config', 'user.name', 'test']):
            self.git(*command)
        (self.root / '.gitignore').write_text('autoresearch/\n')
        (self.root / 'target.py').write_text('slow = True\n')
        (self.root / 'other.py').write_text('untouched = True\n')
        self.git('add', '.')
        self.git('commit', '-q', '-m', 'initial')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True,
                              capture_output=True, text=True).stdout

    def started(self, *after_baseline):
        measure = FakeMeasure(BASE, *after_baseline)
        loop = Loop(self.root, measure=measure)
        loop.start('target.py', 'probe')
        return loop, measure

    def edit(self, name='target.py', text='slow = False\n'):
        (self.root / name).write_text(text)

    def log(self):
        return [line.split('\t') for line in
                (self.root / 'autoresearch' / 'probe.tsv').read_text().splitlines()]

    def head(self):
        return self.git('log', '-1', '--format=%s').strip()

    def test_start_branches_and_records_a_green_baseline(self):
        self.started()
        self.assertEqual(self.git('branch', '--show-current').strip(), 'autoresearch/probe')
        self.assertEqual(self.log()[0], list(autoresearch._COLUMNS))
        self.assertEqual(self.log()[1][1:6], ['10.0', '100', '20', '13', 'baseline'])

    def test_start_refuses_a_red_baseline_a_dirty_tree_or_an_untracked_target(self):
        with self.assertRaisesRegex(SystemExit, 'baseline is not green'):
            Loop(self.root, measure=FakeMeasure(GateFailed('suite'))).start('target.py', 'red')
        self.git('checkout', '-q', 'main')
        self.edit()
        with self.assertRaisesRegex(SystemExit, 'must be clean'):
            Loop(self.root, measure=FakeMeasure(BASE)).start('target.py', 'dirty')
        self.git('checkout', '--', 'target.py')
        with self.assertRaisesRegex(SystemExit, 'not a tracked file'):
            Loop(self.root, measure=FakeMeasure(BASE)).start('missing.py', 'gone')
        with self.assertRaisesRegex(SystemExit, 'tag must be'):
            Loop(self.root, measure=FakeMeasure(BASE)).start('target.py', 'Bad Tag')

    def test_faster_and_green_is_kept_and_committed_alone(self):
        loop, _ = self.started(Measurement(8.0, 100, 20, 13))
        self.edit()
        self.assertEqual(loop.step('drop the sleep'), 'keep')
        self.assertEqual(self.head(), 'autoresearch: drop the sleep')
        self.assertEqual(self.git('show', '--name-only', '--format=').split(), ['target.py'])
        self.assertEqual(self.log()[-1][5], 'keep')

    def test_the_next_step_is_measured_against_the_last_kept_state(self):
        loop, _ = self.started(Measurement(8.0, 100, 20, 13), Measurement(9.0, 100, 20, 13))
        self.edit()
        loop.step('first')
        self.edit(text='slow = None\n')
        self.assertEqual(loop.step('second'), 'discard')

    def test_a_change_outside_the_target_reverts_everything_unmeasured(self):
        loop, measure = self.started()
        self.edit()
        self.edit('other.py', 'untouched = False\n')
        self.assertEqual(loop.step('sneaky'), 'discard')
        self.assertEqual((self.root / 'other.py').read_text(), 'untouched = True\n')
        self.assertEqual((self.root / 'target.py').read_text(), 'slow = True\n')
        self.assertEqual(measure.calls, ['target.py'])  # the baseline only
        self.assertIn('outside the target: other.py', self.log()[-1][6])

    def test_an_untracked_file_is_named_and_not_deleted(self):
        loop, measure = self.started()
        self.edit()
        self.edit('new.py', 'x = 1\n')
        self.assertEqual(loop.step('adds a file'), 'discard')
        self.assertTrue((self.root / 'new.py').exists())
        self.assertEqual(measure.calls, ['target.py'])  # the baseline only

    def test_fewer_tests_refusals_or_attacks_is_not_a_speed_up(self):
        for weaker in (Measurement(5.0, 99, 20, 13), Measurement(5.0, 100, 19, 13),
                       Measurement(5.0, 100, 20, 12)):
            with self.subTest(weaker=weaker):
                loop = Loop(self.root, measure=FakeMeasure(weaker))
                loop.state_path.parent.mkdir(exist_ok=True)
                loop._save({'tag': 'probe', 'target': 'target.py',
                            'best': autoresearch.asdict(BASE)})
                self.edit()
                self.assertEqual(loop.step('delete the slow test'), 'discard')
                self.assertEqual((self.root / 'target.py').read_text(), 'slow = True\n')

    def test_inside_the_noise_is_discarded(self):
        loop, _ = self.started(Measurement(9.8, 100, 20, 13))
        self.edit()
        self.assertEqual(loop.step('barely'), 'discard')
        self.assertIn('not faster beyond noise', self.log()[-1][6])

    def test_a_red_gate_is_a_crash_and_the_target_goes_back(self):
        loop, _ = self.started(GateFailed('the demo did not pass'))
        self.edit()
        self.assertEqual(loop.step('broke it'), 'crash')
        self.assertEqual((self.root / 'target.py').read_text(), 'slow = True\n')
        self.assertEqual(self.log()[-1][5:], ['crash', 'the demo did not pass; broke it'])

    def test_no_change_is_nothing_to_measure(self):
        loop, measure = self.started()
        self.assertEqual(loop.step('forgot to edit'), 'discard')
        self.assertEqual(measure.calls, ['target.py'])

    def test_a_note_cannot_break_the_log(self):
        loop, _ = self.started(Measurement(8.0, 100, 20, 13))
        self.edit()
        loop.step('tab\there\nnewline')
        self.assertEqual(len(self.log()[-1]), len(autoresearch._COLUMNS))

    # --- verify: one number for an external driver ---------------------------

    def commit(self, name='target.py', text='slow = False\n'):
        self.edit(name, text)
        self.git('commit', '-q', '-am', f'experiment: {name}')

    def test_verify_prints_the_number_for_a_committed_change_to_the_target(self):
        loop, _ = self.started(Measurement(8.0, 100, 20, 13))
        self.commit()
        self.assertEqual(loop.verify(), 8.0)

    def test_verify_catches_a_committed_change_outside_the_target(self):
        loop, measure = self.started()
        self.commit('other.py', 'untouched = False\n')
        with self.assertRaisesRegex(SystemExit, 'outside the target: other.py'):
            loop.verify()
        self.assertEqual(measure.calls, ['target.py'])  # the baseline only

    def test_verify_catches_an_untracked_file(self):
        loop, _ = self.started()
        self.edit('new.py', 'x = 1\n')
        with self.assertRaisesRegex(SystemExit, 'outside the target: new.py'):
            loop.verify()

    def test_verify_refuses_fewer_checks_than_the_baseline(self):
        for weaker in (Measurement(5.0, 99, 20, 13), Measurement(5.0, 100, 19, 13),
                       Measurement(5.0, 100, 20, 12)):
            with self.subTest(weaker=weaker):
                loop = Loop(self.root, measure=FakeMeasure(weaker))
                loop.state_path.parent.mkdir(exist_ok=True)
                loop._save({'tag': 'probe', 'target': 'target.py',
                            'best': autoresearch.asdict(BASE),
                            'baseline': autoresearch.asdict(BASE),
                            'base': self.git('rev-parse', 'HEAD').strip()})
                with self.assertRaisesRegex(SystemExit, 'fewer checks'):
                    loop.verify()

    def test_verify_turns_a_red_gate_into_a_refusal(self):
        loop, _ = self.started(GateFailed('the demo did not pass'))
        with self.assertRaisesRegex(SystemExit, 'the demo did not pass'):
            loop.verify()

    def test_step_without_start_says_so(self):
        with self.assertRaisesRegex(SystemExit, 'run `start` first'):
            Loop(self.root, measure=FakeMeasure()).step('x')


if __name__ == '__main__':
    unittest.main()
