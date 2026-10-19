"""The P-01 measurement tool: its arithmetic, not its timings.

Timings belong to the machine that took them and would flake in CI; what is
testable everywhere is that the ladders end where asked, the projections are
the linear/quadratic formulas the report claims, the ceilings come from the
anchor's real bounds, and a miniature run produces one honest row per
checkpoint with a request that grows with the chain.
"""

import unittest

from scripts.bench_audit import (checkpoints_upto, lifetime_ceilings, main,
                                 project_commit_ms, projected_lifetime_seconds,
                                 run_bench)


class BenchAuditTest(unittest.TestCase):
    def test_checkpoints_always_end_at_the_limit(self):
        self.assertEqual(checkpoints_upto((100, 250, 500), 300), (100, 250, 300))
        self.assertEqual(checkpoints_upto((100, 250, 500), 500), (100, 250, 500))
        self.assertEqual(checkpoints_upto((100, 250, 500), 40), (40,))

    def test_projection_formulas_are_linear_and_quadratic(self):
        self.assertEqual(project_commit_ms(10.0, 1000), 10.0)
        # c * n(n+1)/2 with c = 1 s/record: 4 records cost 1+2+3+4.
        self.assertEqual(projected_lifetime_seconds(1_000_000.0, 4), 10.0)

    def test_ceilings_come_from_the_anchor_bounds(self):
        from geniusnew import anchor_process

        # 650 bytes/record at 1000 records: the 64 MiB frame binds far above
        # the 16 MiB state file of 270-byte head lines.
        state, request, binding = lifetime_ceilings(650_000, 1000)
        self.assertEqual(state,
                         anchor_process._MAX_STATE_BYTES // 270)
        self.assertEqual(request, anchor_process._MAX_REQUEST_BYTES // 650)
        self.assertEqual(binding, state)
        # Absurdly large records flip which bound binds.
        state, request, binding = lifetime_ceilings(10**9, 1000)
        self.assertEqual(binding, request)
        self.assertLess(request, state)

    def test_a_miniature_run_reports_a_growing_chain(self):
        result = run_bench(full_commits=20, max_records=40)
        self.assertEqual([row.records for row in result.phase1], [20])
        self.assertEqual([row.records for row in result.phase2], [40])
        row = result.phase2[-1]
        self.assertGreater(row.request_bytes, 0)
        self.assertGreater(row.commit_ms, 0.0)
        self.assertGreater(result.binding_ceiling_records, 40)
        self.assertEqual(result.projected_commit_ms[-1][0],
                         result.binding_ceiling_records)

    def test_unusable_sizes_are_refused_not_measured(self):
        with self.assertRaises(ValueError):
            run_bench(full_commits=5, max_records=100)
        with self.assertRaises(ValueError):
            run_bench(full_commits=100, max_records=50)
        self.assertEqual(main(["--full-commits", "5"]), 2)


if __name__ == "__main__":
    unittest.main()
