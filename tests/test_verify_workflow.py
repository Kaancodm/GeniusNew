"""Regression checks for the workflow's self-hosted runner migration.

Read only the job headers and inline preflight steps used by this workflow;
this is not a YAML validator. Execute the actual preflight with isolated PATH
fixtures so the tests need neither a runner nor application dependencies.
"""

from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / '.github' / 'workflows' / 'verify.yml'
PYTHON_JOBS = ('tests', 'guarded', 'refusals')


class VerifyWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = WORKFLOW.read_text(encoding='utf-8')
        # Anchoring indentation keeps service/step keys out of the job list.
        jobs = source.split('\njobs:\n', 1)[1]
        headers = list(re.finditer(r'^  ([\w-]+):\s*$', jobs, re.MULTILINE))
        cls.jobs = {
            header.group(1): jobs[header.end():
                                 headers[index + 1].start()
                                 if index + 1 < len(headers) else len(jobs)]
            for index, header in enumerate(headers)
        }

    def preflight(self, job):
        matches = list(re.finditer(
            r'^      - name: Use system Python\n'
            r'        run: ([^\n]+)$', self.jobs[job], re.MULTILINE))
        self.assertEqual(len(matches), 1, f'{job}: expected one inline preflight')
        return matches[0]

    def test_all_jobs_use_self_hosted_runners(self):
        self.assertEqual(set(self.jobs), {*PYTHON_JOBS, 'contracts'})
        for job, body in self.jobs.items():
            with self.subTest(job=job):
                self.assertEqual(
                    re.findall(r'^    runs-on: (.+)$', body, re.MULTILINE),
                    ['self-hosted'])

    def test_python_jobs_use_the_system_installation(self):
        for job in PYTHON_JOBS:
            with self.subTest(job=job):
                self.preflight(job)
                self.assertNotRegex(self.jobs[job],
                                    r'uses:\s*actions/setup-python@')

    def test_preflight_runs_after_checkout_before_any_other_command(self):
        for job in PYTHON_JOBS:
            with self.subTest(job=job):
                body = self.jobs[job]
                preflight = self.preflight(job)
                checkout = re.search(r'^      - uses: actions/checkout@',
                                     body, re.MULTILINE)
                self.assertIsNotNone(checkout)
                self.assertLess(checkout.start(), preflight.start())
                commands = list(re.finditer(r'^        run: ', body, re.MULTILINE))
                self.assertGreater(len(commands), 1)
                self.assertLess(preflight.start(), commands[0].start())
                self.assertLess(commands[0].start(), preflight.end())

    def run_preflight(self, command, *, python_status=0, pip_status=0,
                      executable='python3'):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / 'calls'
            if executable is not None:
                stub = root / executable
                stub.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$TRACE"
case "$*" in
    --version) printf 'Python fixture\\n'; exit "$PYTHON_STATUS" ;;
    '-m pip --version') printf 'pip fixture\\n'; exit "$PIP_STATUS" ;;
    *) exit 99 ;;
esac
''', encoding='utf-8')
                stub.chmod(0o755)
            result = subprocess.run(
                ['/bin/bash', '--noprofile', '--norc', '-e', '-o', 'pipefail',
                 '-c', command],
                cwd=root,
                env={'PATH': str(root), 'TRACE': str(trace),
                     'PYTHON_STATUS': str(python_status),
                     'PIP_STATUS': str(pip_status)},
                capture_output=True, text=True, timeout=2)
            calls = trace.read_text(encoding='utf-8').splitlines() if trace.exists() else []
            return result, calls

    def test_preflight_checks_python_and_its_pip_without_python_alias(self):
        for job in PYTHON_JOBS:
            with self.subTest(job=job):
                result, calls = self.run_preflight(self.preflight(job).group(1))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls, ['--version', '-m pip --version'])
                self.assertEqual(result.stdout, 'Python fixture\npip fixture\n')

    def test_python_failure_stops_before_pip_and_preserves_exit_status(self):
        for job in PYTHON_JOBS:
            for status in (1, 42, 127):
                with self.subTest(job=job, status=status):
                    result, calls = self.run_preflight(
                        self.preflight(job).group(1), python_status=status)
                    self.assertEqual(result.returncode, status, result.stderr)
                    self.assertEqual(calls, ['--version'])

    def test_missing_or_broken_pip_fails_the_preflight(self):
        for job in PYTHON_JOBS:
            for status in (1, 42, 127):
                with self.subTest(job=job, status=status):
                    result, calls = self.run_preflight(
                        self.preflight(job).group(1), pip_status=status)
                    self.assertEqual(result.returncode, status, result.stderr)
                    self.assertEqual(calls, ['--version', '-m pip --version'])

    def test_missing_python3_fails_even_when_python_alias_exists(self):
        for job in PYTHON_JOBS:
            for executable in (None, 'python'):
                with self.subTest(job=job, executable=executable):
                    result, calls = self.run_preflight(
                        self.preflight(job).group(1), executable=executable)
                    self.assertEqual(result.returncode, 127, result.stderr)
                    self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
