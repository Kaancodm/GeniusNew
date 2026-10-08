"""The restore drill selects only its exact disposable loopback container."""

import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from geniusnew.contracts import ContractError

spec = importlib.util.spec_from_file_location(
    "demo_restore", Path(__file__).resolve().parents[1] / "scripts/demo_restore.py")
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


class RestoreSelectionTest(unittest.TestCase):
    def select(self, port="49153", *, binding=None, error=None, output=None, **overrides):
        environment = {
            "GITHUB_ACTIONS": "true", "GENIUSNEW_TEST_POSTGRES_CONTAINER": "a" * 64,
            "GENIUSNEW_TEST_ADMIN_DSN":
                f"host=127.0.0.1 port={port} dbname=geniusnew_test_admin user=postgres",
        }
        environment.update(overrides)
        if binding is None:
            binding = {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": port}]}
        result = subprocess.CompletedProcess([], 0,
                                             json.dumps(binding) if output is None else output, "")
        with patch.dict(restore.os.environ, environment, clear=True), \
                patch.object(restore.subprocess, "run", return_value=result,
                             side_effect=error) as inspect:
            value = restore.ci_container()
            self.assertEqual(inspect.call_args.args[0][-1], "a" * 64)
            return value

    def test_dynamic_and_fixed_ports_match_the_exact_container(self):
        for port in ("1", "5432", "49153", "65535"):
            with self.subTest(port=port):
                self.assertEqual(self.select(port), "a" * 64)

    def test_missing_invalid_or_defaulted_ports_are_refused(self):
        for port in ("", "0", "65536", "-1", "005432", "5432,5433"):
            with self.subTest(port=port), self.assertRaises(ContractError):
                self.select(port)
        with self.assertRaises(ContractError):
            self.select(GENIUSNEW_TEST_ADMIN_DSN=
                        "host=127.0.0.1 dbname=geniusnew_test_admin user=postgres")

    def test_wrong_service_or_extra_connection_parameters_are_refused(self):
        for dsn in (
            "host=remote port=49153 dbname=geniusnew_test_admin user=postgres",
            "host=127.0.0.1 port=49153 dbname=production user=postgres",
            "host=127.0.0.1 port=49153 dbname=geniusnew_test_admin user=other",
            "host=127.0.0.1 port=49153 dbname=geniusnew_test_admin user=postgres options=-csearch_path=public",
        ):
            with self.subTest(dsn=dsn), self.assertRaises(ContractError):
                self.select(GENIUSNEW_TEST_ADMIN_DSN=dsn)

    def test_container_identity_and_ci_environment_are_required(self):
        for overrides in (
            {"GITHUB_ACTIONS": "false"}, {"GENIUSNEW_TEST_POSTGRES_CONTAINER": "postgres"},
            {"GENIUSNEW_TEST_POSTGRES_CONTAINER": ""},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ContractError):
                self.select(**overrides)

    def test_mismatching_public_missing_or_multiple_bindings_are_refused(self):
        for binding in (
            [], {}, {"5432/tcp": None},
            {"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49153"}]},
            {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5432"}]},
            {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49153"},
                          {"HostIp": "::", "HostPort": "49153"}]},
        ):
            with self.subTest(binding=binding), self.assertRaises(ContractError):
                self.select(binding=binding)

    def test_unavailable_or_malformed_inspection_refuses(self):
        for error in (OSError(), subprocess.TimeoutExpired("docker", 10),
                      subprocess.CalledProcessError(1, "docker")):
            with self.assertRaises(ContractError):
                self.select(error=error)
        with self.assertRaises(ContractError):
            self.select(output="not JSON")
