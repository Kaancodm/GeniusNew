"""Gate C1: a server starts only from a configuration it can vouch for."""

import copy
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from geniusnew.config import DEMO_ROOT_SECRET, load_config, parse_config, read_root_secret
from geniusnew.contracts import ContractError

EXAMPLE = Path(__file__).resolve().parent.parent / "docs" / "examples" / "geniusnew.toml"

# Zero-entropy and self-describing, like the other canaries in this suite.
ROOT_SECRET = b"ROOT-SECRET-CANARY-MUST-NOT-BE-DISCLOSED"
API_KEY = b"API-KEY-CANARY-FOR-THE-CONFIG-TESTS"
DIGEST = hashlib.sha256(API_KEY).hexdigest()


class Files:
    """A private root secret file and an anchor directory, per test."""

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.secret_path = self.write_secret(ROOT_SECRET)
        self.anchor_path = str(self.root / "anchor.state")

    def tearDown(self):
        self._directory.cleanup()

    def write_secret(self, content, name="root_secret", mode=0o600):
        path = self.root / name
        path.write_bytes(content)
        os.chmod(path, mode)
        return str(path)

    def data(self):
        return {
            "service": {
                "listen_host": "127.0.0.1",
                "listen_port": 8080,
                "root_secret_file": self.secret_path,
                "anchor_state": self.anchor_path,
            },
            "policy": {
                "version": "policy-v1",
                "orchestrator_id": "orchestrator-1",
                "handoff_ttl_seconds": 60,
                "allowed_tools": ["summarize"],
                "allowed_sandbox_profiles": ["isolated"],
                "grants": [{
                    "subject": "subject-a",
                    "user_id": "user-a",
                    "worker_agent_id": "worker-a",
                    "tier": "basic",
                    "tools": ["summarize"],
                    "sandbox_profile": "isolated",
                    "requires_approval": False,
                }],
            },
            "principals": {DIGEST: "subject-a"},
        }


class ValidConfigurationTest(Files, unittest.TestCase):

    def test_a_complete_configuration_is_accepted(self):
        config = parse_config(self.data())
        self.assertEqual(config.listen_host, "127.0.0.1")
        self.assertEqual(config.listen_port, 8080)
        self.assertEqual(config.root_secret, ROOT_SECRET)
        self.assertEqual(config.anchor_state, self.anchor_path)
        self.assertEqual(config.principals, {DIGEST: "subject-a"})
        self.assertEqual([worker.tool for worker in config.workers], ["summarize"])

    def test_ipv6_loopback_is_accepted(self):
        data = self.data()
        data["service"]["listen_host"] = "::1"
        self.assertEqual(parse_config(data).listen_host, "::1")

    def test_the_repr_does_not_disclose_the_root_secret(self):
        self.assertNotIn(ROOT_SECRET.decode(), repr(parse_config(self.data())))

    def test_the_secret_is_used_byte_for_byte(self):
        secret = ROOT_SECRET + b"\n"
        self.secret_path = self.write_secret(secret, name="with_newline")
        self.assertEqual(parse_config(self.data()).root_secret, secret)

    def test_the_example_configuration_parses_once_its_paths_are_real(self):
        text = EXAMPLE.read_text()
        text = text.replace("/etc/geniusnew/root_secret", self.secret_path)
        text = text.replace("/var/lib/geniusnew/anchor.state", self.anchor_path)
        path = self.root / "example.toml"
        path.write_text(text)
        config = load_config(str(path))
        self.assertEqual(config.listen_host, "127.0.0.1")
        # The placeholder is not a digest, so the example cannot start a service.
        self.assertIn("REPLACE_WITH_SHA256_OF_THE_API_KEY", config.principals)


class StructureTest(Files, unittest.TestCase):

    def refused(self, data, message):
        with self.assertRaisesRegex(ContractError, message):
            parse_config(data)

    def test_a_configuration_that_is_not_a_table_is_refused(self):
        self.refused(["service"], "must be a table")

    def test_an_unknown_top_level_key_is_refused(self):
        data = self.data()
        data["extra"] = {}
        self.refused(data, "unknown keys: extra")

    def test_a_missing_top_level_key_is_refused(self):
        data = self.data()
        del data["principals"]
        self.refused(data, "missing keys: principals")

    def test_a_misspelt_grant_key_is_refused_not_defaulted(self):
        data = self.data()
        grant = data["policy"]["grants"][0]
        grant["requires_aproval"] = grant.pop("requires_approval")
        self.refused(data, "unknown keys: requires_aproval")

    def test_an_unknown_service_key_is_refused(self):
        data = self.data()
        data["service"]["tls"] = True
        self.refused(data, "unknown keys: tls")

    def test_grants_that_are_not_an_array_are_refused(self):
        data = self.data()
        data["policy"]["grants"] = 3
        self.refused(data, "must be an array of tables")

    def test_empty_grants_are_refused(self):
        data = self.data()
        data["policy"]["grants"] = []
        self.refused(data, "non-empty")

    def test_tools_that_are_not_a_list_of_strings_are_refused(self):
        data = self.data()
        data["policy"]["allowed_tools"] = "summarize"
        self.refused(data, "must be a list of strings")

    def test_a_policy_the_contract_rejects_is_refused(self):
        data = self.data()
        data["policy"]["handoff_ttl_seconds"] = 3600
        self.refused(data, "handoff_ttl_seconds")


class ListenerTest(Files, unittest.TestCase):

    def refused(self, host=None, port=None, message=""):
        data = self.data()
        if host is not None:
            data["service"]["listen_host"] = host
        if port is not None:
            data["service"]["listen_port"] = port
        with self.assertRaisesRegex(ContractError, message):
            parse_config(data)

    def test_every_interface_is_refused(self):
        self.refused(host="0.0.0.0", message="loopback")

    def test_a_public_address_is_refused(self):
        self.refused(host="203.0.113.7", message="loopback")

    def test_a_hostname_is_refused(self):
        # "localhost" resolves through a file an attacker on the host may edit.
        self.refused(host="localhost", message="literal IP address")

    def test_an_integer_host_is_refused(self):
        # ipaddress would read 2130706433 as 127.0.0.1; the listener needs a string.
        self.refused(host=2130706433, message="non-empty string")

    def test_port_zero_is_refused(self):
        self.refused(port=0, message="between 1 and 65535")

    def test_a_port_out_of_range_is_refused(self):
        self.refused(port=70000, message="between 1 and 65535")

    def test_a_boolean_port_is_refused(self):
        self.refused(port=True, message="between 1 and 65535")


class RootSecretTest(Files, unittest.TestCase):

    def test_a_relative_path_is_refused(self):
        data = self.data()
        data["service"]["root_secret_file"] = "root_secret"
        with self.assertRaisesRegex(ContractError, "absolute path"):
            parse_config(data)

    def test_a_non_string_path_is_refused(self):
        data = self.data()
        data["service"]["root_secret_file"] = 7
        with self.assertRaisesRegex(ContractError, "non-empty string"):
            parse_config(data)

    def test_a_missing_file_is_refused(self):
        with self.assertRaisesRegex(ContractError, "cannot be opened"):
            read_root_secret(str(self.root / "absent"))

    def test_a_symlink_is_refused(self):
        link = self.root / "link"
        link.symlink_to(self.secret_path)
        with self.assertRaisesRegex(ContractError, "cannot be opened without following links"):
            read_root_secret(str(link))

    def test_a_directory_is_refused(self):
        directory = self.root / "secret-dir"
        directory.mkdir(mode=0o700)
        with self.assertRaisesRegex(ContractError, "regular file"):
            read_root_secret(str(directory))

    def test_a_fifo_is_refused_without_blocking(self):
        fifo = self.root / "fifo"
        os.mkfifo(fifo, 0o600)
        with self.assertRaisesRegex(ContractError, "regular file"):
            read_root_secret(str(fifo))

    def test_a_file_readable_by_the_group_is_refused(self):
        path = self.write_secret(ROOT_SECRET, name="group", mode=0o640)
        with self.assertRaisesRegex(ContractError, "group or others"):
            read_root_secret(path)

    def test_a_file_writable_by_others_is_refused(self):
        path = self.write_secret(ROOT_SECRET, name="others", mode=0o602)
        with self.assertRaisesRegex(ContractError, "group or others"):
            read_root_secret(path)

    def test_a_file_owned_by_another_user_is_refused(self):
        owner = os.stat(self.secret_path).st_uid
        with mock.patch("geniusnew.config.os.geteuid", return_value=owner + 1):
            with self.assertRaisesRegex(ContractError, "owned by the service user"):
                read_root_secret(self.secret_path)

    def test_a_short_secret_is_refused(self):
        path = self.write_secret(b"x" * 31, name="short")
        with self.assertRaisesRegex(ContractError, "at least 32 bytes"):
            read_root_secret(path)

    def test_an_oversized_secret_is_refused(self):
        path = self.write_secret(b"x" * 4097, name="large")
        with self.assertRaisesRegex(ContractError, "at most 4096 bytes"):
            read_root_secret(path)

    def test_the_largest_allowed_secret_is_accepted(self):
        path = self.write_secret(b"x" * 4096, name="largest")
        self.assertEqual(len(read_root_secret(path)), 4096)

    def test_the_published_demo_secret_is_refused(self):
        path = self.write_secret(DEMO_ROOT_SECRET, name="demo")
        with self.assertRaisesRegex(ContractError, "demo root secret"):
            read_root_secret(path)


class AnchorStateTest(Files, unittest.TestCase):

    def test_a_relative_anchor_state_is_refused(self):
        data = self.data()
        data["service"]["anchor_state"] = "anchor.state"
        with self.assertRaisesRegex(ContractError, "absolute path"):
            parse_config(data)

    def test_an_anchor_state_in_a_missing_directory_is_refused(self):
        data = self.data()
        data["service"]["anchor_state"] = str(self.root / "absent" / "anchor.state")
        with self.assertRaisesRegex(ContractError, "existing directory"):
            parse_config(data)


class PrincipalsAndWorkersTest(Files, unittest.TestCase):

    def test_no_principals_are_refused(self):
        data = self.data()
        data["principals"] = {}
        with self.assertRaisesRegex(ContractError, "non-empty table"):
            parse_config(data)

    def test_principals_that_are_not_a_table_are_refused(self):
        data = self.data()
        data["principals"] = [DIGEST]
        with self.assertRaisesRegex(ContractError, "non-empty table"):
            parse_config(data)

    def test_a_principal_whose_subject_is_not_a_string_is_refused(self):
        data = self.data()
        data["principals"] = {DIGEST: ["subject-a"]}
        with self.assertRaisesRegex(ContractError, "subjects as strings"):
            parse_config(data)

    def test_a_principal_without_a_grant_is_refused(self):
        data = self.data()
        data["principals"] = {DIGEST: "subject-without-grant"}
        with self.assertRaisesRegex(ContractError, "no grant in the policy"):
            parse_config(data)

    def test_a_plaintext_key_in_place_of_a_digest_is_not_echoed(self):
        data = self.data()
        data["principals"] = {API_KEY.decode(): "subject-without-grant"}
        with self.assertRaises(ContractError) as refused:
            parse_config(data)
        self.assertNotIn(API_KEY.decode()[:8], str(refused.exception))

    def test_a_tool_without_a_built_in_worker_is_refused(self):
        data = self.data()
        data["policy"]["allowed_tools"] = ["summarize", "shell"]
        with self.assertRaisesRegex(ContractError, "no built-in worker for tools: shell"):
            parse_config(data)

    def test_the_parsed_data_is_not_mutated(self):
        data = self.data()
        before = copy.deepcopy(data)
        parse_config(data)
        self.assertEqual(data, before)


class LoadConfigTest(Files, unittest.TestCase):

    def test_a_missing_file_is_refused(self):
        with self.assertRaisesRegex(ContractError, "cannot be read"):
            load_config(str(self.root / "absent.toml"))

    def test_invalid_toml_is_refused(self):
        path = self.root / "broken.toml"
        path.write_text("[service\n")
        with self.assertRaisesRegex(ContractError, "not valid UTF-8 TOML"):
            load_config(str(path))

    def test_invalid_utf8_is_refused(self):
        path = self.root / "latin1.toml"
        path.write_bytes(b"x = \"\xff\"\n")
        with self.assertRaisesRegex(ContractError, "not valid UTF-8 TOML"):
            load_config(str(path))

    def test_an_oversized_file_is_refused(self):
        path = self.root / "large.toml"
        path.write_bytes(b"#" * (1024 * 1024 + 1))
        with self.assertRaisesRegex(ContractError, "at most"):
            load_config(str(path))


if __name__ == "__main__":
    unittest.main()
