"""The grant route carries only the subject resolved by the HTTP registry."""

import unittest

from geniusnew.contracts import ContractError
from tests.test_http_entry import API_KEY, Fixture, Recorder


class GrantRouteTest(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.grant = Recorder(returns={"status": "APPROVAL_GRANTED",
                                       "approval_token": "ab" * 32})
        self.entry = self.entry_for(grant_approval=self.grant)

    def decide(self, *, body=b"{}", key=API_KEY, path="/approvals/job-7/grant"):
        return self.post(body, key=key, path=path)

    def test_only_the_authenticated_subject_and_routed_job_are_passed(self):
        response = self.decide()
        self.assertEqual(response.status, 202)
        self.assertEqual(response.body["job_id"], "job-7")
        self.assertEqual(self.grant.calls, [{"subject": "subject-demo",
                                            "job_id": "job-7"}])
        self.assertEqual(self.submit.calls, [])

    def test_no_callback_means_no_route(self):
        self.assertEqual(self.post(b"{}", entry=self.entry_for(),
                                   path="/approvals/job-7/grant").status, 404)
        self.assertEqual(self.grant.calls, [])

    def test_malformed_and_privilege_bearing_bodies_reach_nothing(self):
        for body in (b"", b"{", b"[]", b"null", b"\xff", b'{"subject":"forged"}',
                     b'{"role":"APPROVER"}', b'{"approval_token":"ab"}'):
            with self.subTest(body=body):
                self.assertEqual(self.decide(body=body).status, 400)
        self.assertEqual(self.grant.calls, [])

    def test_only_an_exact_path_is_accepted(self):
        for path in ("/approvals//grant", "/approvals/job 7/grant",
                     "/approvals/job-7/grant?x=1", "/approvals/job-7/grant/x",
                     "/approvals/" + "a" * 65 + "/grant"):
            self.assertEqual(self.decide(path=path).status, 404)
        self.assertEqual(self.grant.calls, [])

    def test_authentication_precedes_the_decision(self):
        self.assertEqual(self.decide(key=None).status, 401)
        self.assertEqual(self.decide(key=b"UNKNOWN-KEY-CANARY-FOR-C3").status, 401)
        self.assertEqual(self.grant.calls, [])

    def test_a_bad_callback_is_refused_at_construction(self):
        for callback in (7, "grant", {}):
            with self.assertRaisesRegex(ContractError, "grant_approval"):
                self.entry_for(grant_approval=callback)

    def test_business_refusals_cannot_disclose_job_or_role_details(self):
        self.entry = self.entry_for(grant_approval=Recorder(
            raises=ContractError("private role or pending job detail")))
        response = self.decide()
        self.assertEqual((response.status, response.body),
                         (409, {"error": "REJECTED"}))
