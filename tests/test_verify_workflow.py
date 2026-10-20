"""Keep the CI test database on a validated loopback service port."""

from pathlib import Path
import re
import subprocess
import tempfile
import unittest


WORKFLOW = Path(__file__).resolve().parent.parent / ".github/workflows/verify.yml"


class VerifyWorkflowDatabaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = WORKFLOW.read_text(encoding="utf-8").split("\njobs:\n", 1)[1]
        headers = list(re.finditer(r"^  ([\w-]+):\s*$", source, re.MULTILINE))
        cls.jobs = {
            header.group(1): source[
                header.end():headers[index + 1].start()
                if index + 1 < len(headers) else len(source)
            ]
            for index, header in enumerate(headers)
        }

    def _database_setup(self, job):
        body = self.jobs[job]
        matches = list(re.finditer(
            r"^      - name: Configure disposable PostgreSQL\n"
            r"        env:\n"
            r"          POSTGRES_PORT: (.+)\n"
            r"        run: \|\n"
            r"((?:          [^\n]*\n)+)",
            body, re.MULTILINE,
        ))
        self.assertEqual(len(matches), 1, f"{job}: expected one database setup step")
        self.assertEqual(matches[0].group(1),
                         "${{ job.services.postgres.ports['5432'] }}")
        self.assertLess(matches[0].start(), body.index("      - name: Install "))
        return "\n".join(line[10:] for line in matches[0].group(2).splitlines())

    def _run_setup(self, command, port):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / "github-env"
            environment = {"GITHUB_ENV": str(env_file)}
            if port is not None:
                environment["POSTGRES_PORT"] = port
            result = subprocess.run(
                ["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail",
                 "-c", command],
                cwd=directory, env=environment, capture_output=True, text=True,
                timeout=2,
            )
            content = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
            return result, content

    def test_trust_database_is_bound_to_a_dynamic_loopback_port(self):
        for job in ("tests", "refusals"):
            with self.subTest(job=job):
                body = self.jobs[job]
                port_blocks = re.findall(
                    r"(?m)^        ports:\n((?:          - [^\n]+\n)+)", body)
                self.assertEqual(port_blocks, ["          - '127.0.0.1::5432'\n"])
                self.assertEqual(body.count("GENIUSNEW_TEST_ADMIN_DSN"), 1)
                self._database_setup(job)

    def test_assigned_service_port_reaches_later_steps(self):
        for job in ("tests", "refusals"):
            command = self._database_setup(job)
            for port in ("1", "5432", "49153", "65535"):
                with self.subTest(job=job, port=port):
                    result, content = self._run_setup(command, port)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(content,
                                     "GENIUSNEW_TEST_ADMIN_DSN=host=127.0.0.1 "
                                     f"port={port} dbname=geniusnew_test_admin "
                                     "user=postgres\n")

    def test_missing_or_invalid_service_port_exports_no_dsn(self):
        for job in ("tests", "refusals"):
            command = self._database_setup(job)
            for port in (None, "", "0", "00001", "65536", "99999999999999999999", "-1",
                         "localhost:5432", "5432\nINJECTED=yes"):
                with self.subTest(job=job, port=port):
                    result, content = self._run_setup(command, port)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(content, "")


if __name__ == "__main__":
    unittest.main()
