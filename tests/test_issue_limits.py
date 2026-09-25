import unittest

from geniusnew.contracts import ContractError, Grant, HandoffSigner, Policy, issue


class IssueWireLimitTest(unittest.TestCase):
    def test_issue_rejects_wire_that_gateway_would_reject_for_size(self):
        key = HandoffSigner(integrity_key=b'wire-limit-test-integrity-key-32bytes')
        grant = Grant(
            'subject-demo', 'user-demo', 'worker-demo', 'basic',
            ('summarize',), 'isolated', False,
        )
        policy = Policy(
            'policy-v1', 'orchestrator-demo', 60,
            ('summarize',), ('isolated',), (grant,),
        )

        with self.assertRaises(ContractError):
            issue(
                {'text': 'x' * 20_000},
                subject='subject-demo',
                job_id='job-demo',
                policy=policy,
                signer=key,
                now=100,
            )


if __name__ == '__main__':
    unittest.main()
