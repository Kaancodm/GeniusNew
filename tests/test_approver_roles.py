"""Gate C3: a server-configured approver can decide only once, never for itself."""

import json
import hashlib
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from geniusnew.audit_chain import AuditAnchor
from geniusnew.contracts import ContractError, Grant, Policy
from geniusnew.keys import derive_keys
from geniusnew.results import WorkerAuthority
from geniusnew.wiring import PendingJobs, _Waiting, build
from geniusnew.workers import DeterministicSummarizer, WorkerRunner
from tests.test_end_to_end import ROOT_SECRET

OWNER_KEY = b"OWNER-KEY-CANARY-FOR-C3-TEST-ONLY"
OTHER_KEY = b"OTHER-KEY-CANARY-FOR-C3-TEST-ONLY"
APPROVER_KEY = b"APPROVER-KEY-CANARY-FOR-C3-TEST-ONLY"
ALIAS_KEY = b"ALIAS-KEY-CANARY-FOR-C3-TEST-ONLY"


class ApproverServiceTest(unittest.TestCase):
    def setUp(self):
        self.clock = [1_700_000_000]
        self.ids = iter(("job-one", "job-two", "job-three"))
        grants = tuple(Grant(subject, user, "worker-c3", "basic",
                             ("summarize",), "isolated", True)
                       for subject, user in (("owner", "user-owner"),
                                             ("other", "user-other")))
        self.policy = Policy("policy-v1", "orchestrator-1", 60,
                             ("summarize",), ("isolated",), grants)
        keys = derive_keys(ROOT_SECRET)
        authority = WorkerAuthority(result_key=keys.result_key,
                                    integrity_key=keys.integrity_key)
        self.service = build(
            root_secret=ROOT_SECRET, policy=self.policy,
            api_keys={OWNER_KEY: "owner", OTHER_KEY: "other",
                      APPROVER_KEY: "approver", ALIAS_KEY: "owner-alias"},
            approvers={"approver": "user-approver", "owner": "user-owner",
                       "owner-alias": "user-owner"},
            workers=(DeterministicSummarizer(),), anchor=AuditAnchor(),
            clock=lambda: self.clock[0], job_ids=lambda: next(self.ids),
            runner_factory=lambda worker: WorkerRunner(worker, authority=authority))
        self.addCleanup(self.service.close)

    def request(self, path, *, key=OWNER_KEY, body=b"{}", token=None):
        headers = {"Content-Type": "application/json",
                   "Authorization": "Bearer " + key.decode()}
        if token is not None:
            headers["X-Approval-Token"] = token
        return self.service.entry.handle(method="POST", path=path,
                                         headers=headers, body=body)

    def pending(self, *, key=OWNER_KEY):
        response = self.request("/jobs", key=key, body=b'{"text":"safe work"}')
        self.assertEqual((response.status, response.body["status"]),
                         (202, "PENDING_APPROVAL"))
        return response.body["job_id"]

    def grant(self, job, **arguments):
        return self.request("/approvals/" + job + "/grant",
                            key=arguments.pop("key", APPROVER_KEY), **arguments)

    def test_a_foreign_role_cannot_grant(self):
        job = self.pending()
        self.assertEqual(self.grant(job, key=OTHER_KEY).body, {"error": "REJECTED"})
        self.assertEqual(self.grant(job, key=OTHER_KEY).status, 409)
        self.assertEqual(len(self.service.chain.records), 1)

    def test_an_approver_cannot_grant_its_own_job(self):
        job = self.pending()
        self.assertEqual(self.grant(job, key=OWNER_KEY).status, 409)
        self.assertEqual(len(self.service.approvals._records), 0)

    def test_a_subject_alias_cannot_approve_the_same_persons_job(self):
        job = self.pending()
        self.assertEqual(self.grant(job, key=ALIAS_KEY).status, 409)
        self.assertEqual(len(self.service.approvals._records), 0)

    def test_a_second_decision_cannot_mint_a_second_token(self):
        job = self.pending()
        self.assertEqual(self.grant(job).status, 202)
        self.assertEqual(self.grant(job).status, 409)
        self.assertEqual(len(self.service.approvals._records), 1)
        self.assertEqual([r.event.action for r in self.service.chain.records],
                         ["HANDOFF_ISSUED", "APPROVAL_GRANTED"])
        self.assertEqual(
            self.service.chain.records[1].event.api_subject_sha256,
            hashlib.sha256(b"approver").hexdigest())

    def test_concurrent_decisions_have_exactly_one_winner(self):
        job = self.pending()
        start = threading.Barrier(3)

        def decide():
            start.wait(timeout=3)
            return self.grant(job).status

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(decide), pool.submit(decide)
            start.wait(timeout=3)
            self.assertEqual(sorted((first.result(3), second.result(3))), [202, 409])
        self.assertEqual(len(self.service.approvals._records), 1)

    def test_client_identity_or_rights_never_reach_a_decision(self):
        job = self.pending()
        for field in ("subject", "user_id", "tier", "tools", "job_id", "role",
                      "approver_subject", "approval_token", "ttl_seconds"):
            with self.subTest(field=field):
                self.assertEqual(self.grant(
                    job, body=json.dumps({field: "forged"}).encode()).status, 400)
        self.assertEqual(len(self.service.approvals._records), 0)

    def test_unknown_and_expired_jobs_are_indistinguishable(self):
        job = self.pending()
        self.clock[0] += 60
        for candidate in (job, "job-missing"):
            self.assertEqual((self.grant(candidate).status,
                              self.grant(candidate).body),
                             (409, {"error": "REJECTED"}))

    def test_approval_has_to_be_anchored_before_a_token_is_returned(self):
        job = self.pending()
        with patch.object(self.service.anchor, "commit",
                          side_effect=ContractError("anchor unavailable")):
            response = self.grant(job)
        self.assertEqual((response.status, response.body),
                         (409, {"error": "REJECTED"}))
        # The ambiguous decision remains closed even when the anchor recovers.
        self.assertEqual(self.grant(job).status, 409)
        self.assertEqual(len(self.service.approvals._records), 1)

    def test_the_owner_alone_presents_the_token_and_it_runs_once(self):
        job = self.pending()
        granted = self.grant(job)
        self.assertEqual(granted.status, 202)
        token = granted.body["approval_token"]
        self.assertRegex(token, r"\A[0-9a-f]{64}\Z")
        self.assertEqual(self.request("/jobs/" + job + "/approve",
                                      key=OTHER_KEY, token=token).status, 409)
        completed = self.request("/jobs/" + job + "/approve", token=token)
        self.assertEqual((completed.status, completed.body["status"]),
                         (202, "SUCCEEDED"))
        self.assertEqual(self.request("/jobs/" + job + "/approve", token=token).status, 409)
        self.assertEqual(self.grant(job).status, 409)
        granted_hash = self.service.chain.records[1].event.approval_record_hash
        accepted_hash = self.service.chain.records[-1].event.approval_record_hash
        self.assertIsNotNone(granted_hash)
        self.assertIsNotNone(accepted_hash)
        self.assertNotEqual(granted_hash, accepted_hash)

    def test_an_approver_without_a_worker_grant_cannot_submit(self):
        self.assertEqual(self.request("/jobs", key=APPROVER_KEY,
                                     body=b'{"text":"safe work"}').status, 409)
        self.assertEqual(self.service.chain.records, ())

    def test_no_direct_service_path_bypasses_the_role(self):
        job = self.pending()
        with self.assertRaises(ContractError):
            self.service.approve(job)
        for subject in ("other", "owner", "owner-alias", "unknown"):
            with self.subTest(subject=subject):
                with self.assertRaises(ContractError):
                    self.service.approve(job, approver_subject=subject)
        self.assertEqual(len(self.service.approvals._records), 0)

    def test_pending_decision_rejects_invalid_token_lifetime(self):
        for ttl in (0, 601, True, "30"):
            with self.subTest(ttl=ttl):
                pending = PendingJobs()
                pending.add("job", _Waiting("owner", b"wire",
                                           SimpleNamespace(user_id="user-owner",
                                                           expires_at=100), "trace"),
                            now=1)
                with self.assertRaises(ContractError):
                    pending.decide("job", approver_user_id="user-approver",
                                   grant=lambda waiting: b"token", now=2,
                                   ttl_seconds=ttl)


    def test_the_grant_route_works_over_a_real_socket(self):
        import http.client
        from geniusnew.http_entry import serve

        server = serve(self.service.entry)
        loop = threading.Thread(target=server.serve_forever,
                                kwargs={"poll_interval": 0.01}, daemon=True)
        loop.start()
        try:
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                def request(path, key, body, token=None):
                    headers = {"Content-Type": "application/json",
                               "Authorization": "Bearer " + key.decode()}
                    if token is not None:
                        headers["X-Approval-Token"] = token
                    connection.request("POST", path, body=body, headers=headers)
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())

                status, pending = request("/jobs", OWNER_KEY, b'{"text":"safe work"}')
                self.assertEqual(status, 202)
                job = pending["job_id"]
                path = "/approvals/" + job + "/grant"
                self.assertEqual(request(path, OWNER_KEY, b"{}"),
                                 (409, {"error": "REJECTED"}))
                status, granted = request(path, APPROVER_KEY, b"{}")
                self.assertEqual(status, 202)
                status, result = request("/jobs/" + job + "/approve", OWNER_KEY,
                                         b"{}", token=granted["approval_token"])
                self.assertEqual((status, result["status"]), (202, "SUCCEEDED"))
                self.assertEqual(len(self.service.chain.records), 5)
            finally:
                connection.close()
        finally:
            server.shutdown()
            server.server_close()
            loop.join(5)
        self.assertFalse(loop.is_alive())

    def test_roles_are_copied_from_configuration(self):
        role_map = {"approver": "user-approver"}
        # Construction owns the trusted snapshot, not a caller-mutable table.
        from geniusnew.approvals import ApproverPolicy
        roles = ApproverPolicy(role_map, policy=self.policy,
                               principal_subjects=("approver",))
        role_map["owner"] = "user-owner"
        with self.assertRaises(ContractError):
            roles.user_id("owner")
        self.assertEqual(roles.user_id("approver"), "user-approver")

class ApproverPolicyTest(unittest.TestCase):
    def setUp(self):
        grant = Grant("owner", "user-owner", "worker-c3", "basic",
                      ("summarize",), "isolated", True)
        self.policy = Policy("policy-v1", "orchestrator-1", 60,
                             ("summarize",), ("isolated",), (grant,))

    def roles(self, mapping):
        from geniusnew.approvals import ApproverPolicy
        return ApproverPolicy(mapping, policy=self.policy,
                              principal_subjects=("owner", "approver", "x" * 161))

    def test_unusable_role_configuration_is_refused(self):
        cases = (None, [], "roles", {"approver": ""},
                 {"": "user"}, {"approver": None}, {"approver": 1},
                 {42: "user"}, {"approver": "x" * 161},
                 {"x" * 161: "user"}, {"unknown": "user"},
                 {"owner": "different-user"})
        for mapping in cases:
            with self.subTest(mapping=repr(mapping)[:70]):
                with self.assertRaises(ContractError):
                    self.roles(mapping)

    def test_no_role_is_inferred_from_a_policy_grant(self):
        roles = self.roles({})
        self.assertEqual(len(roles), 0)
        for subject in ("owner", None, [], 7):
            with self.subTest(subject=subject):
                with self.assertRaises(ContractError):
                    roles.user_id(subject)

    def test_a_matching_policy_identity_is_accepted(self):
        self.assertEqual(self.roles({"owner": "user-owner"}).user_id("owner"),
                         "user-owner")
