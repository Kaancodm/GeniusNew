"""Regression checks for isolated runners and disposable CI database setup.

Read the job headers and setup steps used by this workflow, not arbitrary YAML.
Execute the actual setup commands with isolated environments so these tests need
neither a runner nor application dependencies.
"""

from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / '.github' / 'workflows' / 'verify.yml'
PYTHON_JOBS = ('tests', 'guarded', 'refusals')
POSTGRES_JOBS = ('tests', 'refusals')


class VerifyWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Extract top-level job bodies for the workflow assertions."""
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
        """Return the job's preflight match, requiring exactly one inline step."""
        matches = list(re.finditer(
            r'^      - name: Check configured Python\n'
            r'        run: ([^\n]+)$', self.jobs[job], re.MULTILINE))
        self.assertEqual(len(matches), 1, f'{job}: expected one inline preflight')
        return matches[0]

    def test_all_jobs_use_disposable_hosted_runners(self):
        """Neither PRs nor main pushes may select the persistent core host."""
        self.assertEqual(set(self.jobs), {*PYTHON_JOBS, 'contracts'})
        for job, body in self.jobs.items():
            with self.subTest(job=job):
                runners = re.findall(r'^    runs-on: (.+)$', body, re.MULTILINE)
                self.assertEqual(len(runners), 1)
                self.assertRegex(runners[0], r'^ubuntu-(latest|\d{2}\.\d{2})$')

    def test_python_jobs_use_a_pinned_setup_and_one_interpreter(self):
        """Installation and checks use the interpreter provided by setup-python."""
        for job in PYTHON_JOBS:
            with self.subTest(job=job):
                self.preflight(job)
                self.assertRegex(self.jobs[job],
                                 r'uses: actions/setup-python@[0-9a-f]{40}\b')
                self.assertEqual(set(re.findall(
                    r'\bpython(?:3(?:\.\d+)?)?(?=\s)', self.jobs[job])), {'python'})

    def test_preflight_runs_after_checkout_before_any_other_command(self):
        """Check the configured interpreter before any install or test command."""
        for job in PYTHON_JOBS:
            with self.subTest(job=job):
                body = self.jobs[job]
                preflight = self.preflight(job)
                checkout = re.search(r'^      - uses: actions/checkout@',
                                     body, re.MULTILINE)
                self.assertIsNotNone(checkout)
                setup = re.search(r'^      - uses: actions/setup-python@',
                                  body, re.MULTILINE)
                self.assertIsNotNone(setup)
                self.assertLess(checkout.start(), setup.start())
                self.assertLess(setup.start(), preflight.start())
                commands = list(re.finditer(r'^        run: ', body, re.MULTILINE))
                self.assertGreater(len(commands), 1)
                self.assertLess(preflight.start(), commands[0].start())
                self.assertLess(commands[0].start(), preflight.end())

    def run_preflight(self, command, *, python_status=0, pip_status=0,
                      executable='python'):
        """Run with an isolated Python stub and return the result and call trace."""
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

    def test_preflight_checks_the_interpreter_used_by_later_steps(self):
        """The same executable must provide Python and pip for later commands."""
        for job in PYTHON_JOBS:
            with self.subTest(job=job):
                result, calls = self.run_preflight(self.preflight(job).group(1))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls, ['--version', '-m pip --version'])
                self.assertEqual(result.stdout, 'Python fixture\npip fixture\n')

    def test_python_failure_stops_before_pip_and_preserves_exit_status(self):
        """Ensure Python failures stop the preflight before pip is invoked."""
        for job in PYTHON_JOBS:
            for status in (1, 42, 127):
                with self.subTest(job=job, status=status):
                    result, calls = self.run_preflight(
                        self.preflight(job).group(1), python_status=status)
                    self.assertEqual(result.returncode, status, result.stderr)
                    self.assertEqual(calls, ['--version'])

    def test_missing_or_broken_pip_fails_the_preflight(self):
        """Ensure pip failures propagate their exit status from the preflight."""
        for job in PYTHON_JOBS:
            for status in (1, 42, 127):
                with self.subTest(job=job, status=status):
                    result, calls = self.run_preflight(
                        self.preflight(job).group(1), pip_status=status)
                    self.assertEqual(result.returncode, status, result.stderr)
                    self.assertEqual(calls, ['--version', '-m pip --version'])

    def test_missing_configured_python_fails_even_with_system_python3(self):
        """A different system interpreter cannot satisfy the checked contract."""
        for job in PYTHON_JOBS:
            for executable in (None, 'python3'):
                with self.subTest(job=job, executable=executable):
                    result, calls = self.run_preflight(
                        self.preflight(job).group(1), executable=executable)
                    self.assertEqual(result.returncode, 127, result.stderr)
                    self.assertEqual(calls, [])

    def database_setup(self, job):
        """Extract the runtime port binding and the actual environment-file script."""
        body = self.jobs[job]
        matches = list(re.finditer(
            r'^      - name: Configure disposable PostgreSQL\n'
            r'        env:\n'
            r'          POSTGRES_PORT: (.+)\n'
            r'        run: \|\n'
            r'((?:          [^\n]*\n)+)', body, re.MULTILINE))
        self.assertEqual(len(matches), 1, f'{job}: expected runtime database setup')
        return matches[0]

    def test_database_ports_are_dynamic_and_loopback_only(self):
        """The disposable trust-authenticated DB must not bind host interfaces."""
        for job in POSTGRES_JOBS:
            with self.subTest(job=job):
                mappings = re.findall(r'^          - (.+)$', self.jobs[job], re.MULTILINE)
                self.assertEqual(len(mappings), 1)
                self.assertRegex(mappings[0], r"^['\"]?127\.0\.0\.1::5432['\"]?$")

    def test_service_context_is_read_only_after_the_service_starts(self):
        """GitHub permits the service-port context at step scope, not job env."""
        for job in POSTGRES_JOBS:
            with self.subTest(job=job):
                body = self.jobs[job]
                job_env = re.search(r'^    env:\n((?:      [^\n]*\n)+)',
                                    body, re.MULTILINE)
                if job_env is not None:
                    self.assertNotIn('job.services', job_env.group(1))
                setup = self.database_setup(job)
                self.assertEqual(setup.group(1),
                                 "${{ job.services.postgres.ports['5432'] }}")
                self.assertLess(setup.start(), body.index('      - name: Install '))

    def run_database_setup(self, job, port):
        """Execute the workflow script without a server or inherited credentials."""
        command = '\n'.join(line[10:] for line in
                            self.database_setup(job).group(2).splitlines())
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / 'github-env'
            env = {'PATH': directory, 'GITHUB_ENV': str(env_file)}
            if port is not None:
                env['POSTGRES_PORT'] = port
            result = subprocess.run(
                ['/bin/bash', '--noprofile', '--norc', '-e', '-o', 'pipefail',
                 '-c', command], env=env, cwd=directory,
                capture_output=True, text=True, timeout=2)
            content = env_file.read_text() if env_file.exists() else ''
            return result, content

    def test_database_setup_exports_the_assigned_port(self):
        """Following steps receive the service's port rather than a default DSN."""
        for job in POSTGRES_JOBS:
            for port in ('1', '5432', '49153', '65535'):
                with self.subTest(job=job, port=port):
                    result, content = self.run_database_setup(job, port)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(content,
                                     'GENIUSNEW_TEST_ADMIN_DSN=host=127.0.0.1 '
                                     f'port={port} dbname=geniusnew_test_admin '
                                     'user=postgres\n')

    def test_missing_or_invalid_service_ports_export_no_dsn(self):
        """Bad service state must not fall back to another DB or inject env lines."""
        for job in POSTGRES_JOBS:
            for port in (None, '', '0', '65536', '99999999999999999999', '-1',
                         'localhost:5432', '5432\nINJECTED=yes'):
                with self.subTest(job=job, port=port):
                    result, content = self.run_database_setup(job, port)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(content, '')

    def test_contracts_refuses_failed_skipped_or_missing_required_checks(self):
        """The aggregate gate only succeeds when tests and every refusal succeeded."""
        body = self.jobs['contracts']
        self.assertRegex(body, r'(?m)^    needs: \[tests, refusals\]$')
        self.assertRegex(body, r'(?m)^    if: always\(\)$')
        self.assertIn('TESTS: ${{ needs.tests.result }}', body)
        self.assertIn('REFUSALS: ${{ needs.refusals.result }}', body)
        match = re.search(r'^        run: \|\n((?:          [^\n]*\n)+)',
                          body, re.MULTILINE)
        self.assertIsNotNone(match)
        command = '\n'.join(line[10:] for line in match.group(1).splitlines())
        for tests in ('success', 'failure', 'skipped', 'cancelled', ''):
            for refusals in ('success', 'failure', 'skipped', 'cancelled', ''):
                with self.subTest(tests=tests, refusals=refusals):
                    result = subprocess.run(
                        ['/bin/bash', '--noprofile', '--norc', '-e', '-o', 'pipefail',
                         '-c', command], env={'TESTS': tests, 'REFUSALS': refusals},
                        capture_output=True, text=True, timeout=2)
                    self.assertEqual(result.returncode == 0,
                                     tests == refusals == 'success')


if __name__ == '__main__':
    unittest.main()
