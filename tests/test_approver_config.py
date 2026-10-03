"""C3 role configuration is explicit and bound to server-owned identities."""

import unittest

from geniusnew.config import parse_config
from geniusnew.contracts import ContractError
from tests.test_config import Files


class ApproverConfigurationTest(Files, unittest.TestCase):
    def data_with_approver(self):
        data = self.data()
        data["principals"]["a" * 64] = "subject-approver"
        data["approvers"] = {"subject-approver": "user-approver"}
        return data

    def test_an_explicit_approver_does_not_need_a_worker_grant(self):
        config = parse_config(self.data_with_approver())
        self.assertEqual(config.approvers, {"subject-approver": "user-approver"})
        self.assertIn("subject-approver", config.principals.values())
        self.assertNotIn("subject-approver",
                         {grant.subject for grant in config.policy.grants})

    def test_missing_or_empty_roles_authorize_no_approver(self):
        for enabled in (False, True):
            data = self.data()
            if enabled:
                data["approvers"] = {}
            self.assertEqual(dict(parse_config(data).approvers), {})

    def test_role_shapes_are_closed_and_fail_closed(self):
        for value in (None, [], "APPROVER", 7, {"subject-a": ["user-a"]},
                      {"subject-a": ""}, {"subject-a": 1}):
            data = self.data()
            data["approvers"] = value
            with self.subTest(value=value):
                with self.assertRaises(ContractError):
                    parse_config(data)

    def test_a_role_without_a_credential_is_refused(self):
        data = self.data()
        data["approvers"] = {"subject-absent": "user-absent"}
        with self.assertRaisesRegex(ContractError, "configured principal"):
            parse_config(data)

    def test_a_dual_role_cannot_have_two_different_user_identities(self):
        data = self.data()
        data["approvers"] = {"subject-a": "user-forged"}
        with self.assertRaisesRegex(ContractError, "identity must match"):
            parse_config(data)

    def test_a_matching_dual_role_is_accepted(self):
        data = self.data()
        data["approvers"] = {"subject-a": "user-a"}
        self.assertEqual(parse_config(data).approvers, {"subject-a": "user-a"})

    def test_configuration_cannot_be_mutated_to_add_a_role_after_parsing(self):
        data = self.data_with_approver()
        config = parse_config(data)
        data["approvers"]["subject-a"] = "user-a"
        self.assertNotIn("subject-a", config.approvers)
        with self.assertRaises(TypeError):
            config.approvers["subject-a"] = "user-a"
