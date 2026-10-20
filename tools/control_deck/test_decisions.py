from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from geniusnew.contracts import ContractError
from tools.control_deck import decisions as d

NOW = 1792526400.0


def sample():
    return {"schema": 1, "repository": d.REPOSITORY, "complete": True,
            "generated_at": datetime.fromtimestamp(NOW, timezone.utc).isoformat(),
            "pull_requests": [{"number": 7, "title": "example", "url": "https://github.com/Kaancodm/GeniusNew/pull/7",
            "headRefOid": "a" * 40, "headRefName": "codex/example", "isDraft": False,
            "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "reviewDecision": "APPROVED",
            "statusCheckRollup": [{"name": "contracts", "workflowName": "Verify contracts", "status": "COMPLETED", "conclusion": "SUCCESS"}],
            "latestReviews": [], "body": "## Offene Punkte\n- Entscheidung zum Modell?"}]}


class DecisionInboxTest(unittest.TestCase):
    def row(self, raw=None, now=NOW):
        return d.normalize(raw or sample(), now)["rows"][0]

    def test_green_checks_never_grant_merge_authority_or_review_approval(self):
        row = self.row()
        self.assertEqual(row["category"], "candidate")
        self.assertFalse(row["merge_authorized"])
        self.assertEqual(row["review_gate"], "UNKNOWN")

    def test_a_same_named_check_from_another_workflow_cannot_stand_in_for_contracts(self):
        raw = sample(); raw["pull_requests"][0]["statusCheckRollup"][0]["workflowName"] = "Other"
        self.assertNotEqual(self.row(raw)["category"], "candidate")

    def test_draft_is_never_a_merge_candidate(self):
        raw = sample(); raw["pull_requests"][0]["isDraft"] = True
        self.assertEqual(self.row(raw)["category"], "working")

    def test_missing_or_skipped_contracts_is_not_pass(self):
        for checks in [[], [{"name": "contracts", "status": "COMPLETED", "conclusion": "SKIPPED"}]]:
            raw = sample(); raw["pull_requests"][0]["statusCheckRollup"] = checks
            self.assertEqual(self.row(raw)["ci"], "UNKNOWN")

    def test_duplicate_contracts_is_ambiguous(self):
        raw = sample(); raw["pull_requests"][0]["statusCheckRollup"] *= 2
        self.assertEqual(self.row(raw)["ci"], "UNKNOWN")

    def test_failed_or_running_checks_block_positive_recommendation(self):
        for status, conclusion, category in [("COMPLETED", "FAILURE", "blocked"), ("IN_PROGRESS", "", "waiting")]:
            raw = sample(); raw["pull_requests"][0]["statusCheckRollup"].append(
                {"name": "other", "status": status, "conclusion": conclusion})
            self.assertEqual(self.row(raw)["category"], category)

    def test_conflict_and_changes_requested_are_blockers(self):
        for field, value in [("mergeable", "CONFLICTING"), ("reviewDecision", "CHANGES_REQUESTED")]:
            raw = sample(); raw["pull_requests"][0][field] = value
            self.assertEqual(self.row(raw)["category"], "blocked")

    def test_only_observed_branch_conflict_is_labelled_conflict(self):
        raw = sample(); raw["pull_requests"][0]["mergeable"] = "CONFLICTING"
        self.assertTrue(self.row(raw)["conflicts"])
        self.assertEqual(self.row()["conflicts"], [])

    def test_old_review_changes_request_is_conservatively_a_blocker(self):
        raw = sample(); raw["pull_requests"][0]["latestReviews"] = [{"state": "CHANGES_REQUESTED"}]
        self.assertEqual(self.row(raw)["category"], "blocked")

    def test_unknown_merge_state_is_not_candidate(self):
        for state in ["BEHIND", "BLOCKED", "UNKNOWN"]:
            raw = sample(); raw["pull_requests"][0]["mergeStateStatus"] = state
            self.assertNotEqual(self.row(raw)["category"], "candidate")

    def test_stale_snapshot_removes_positive_status(self):
        raw = sample(); result = d.normalize(raw, NOW + 301)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["rows"][0]["category"], "stale")

    def test_future_incomplete_or_wrong_repo_is_rejected(self):
        for field, value in [("complete", False), ("repository", "someone/else"),
                             ("generated_at", datetime.fromtimestamp(NOW + 31, timezone.utc).isoformat())]:
            raw = sample(); raw[field] = value
            with self.assertRaises(ValueError):
                d.normalize(raw, NOW)

    def test_duplicate_or_malformed_identity_is_rejected(self):
        for change in [lambda r: r["pull_requests"].append(deepcopy(r["pull_requests"][0])),
                       lambda r: r["pull_requests"][0].update(url="javascript:alert(1)"),
                       lambda r: r["pull_requests"][0].update(headRefOid="a" * 7),
                       lambda r: r["pull_requests"][0].update(number=True)]:
            raw = sample(); change(raw)
            with self.assertRaises(ValueError):
                d.normalize(raw, NOW)

    def test_question_excerpts_are_separate_from_review_approval(self):
        row = self.row()
        self.assertEqual(row["hints"], ["- Entscheidung zum Modell?"])
        self.assertEqual(row["review_gate"], "UNKNOWN")

    def test_reader_refuses_symlink_fifo_and_bad_json_without_process(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(d.subprocess, "run") as run:
            root = Path(tmp); target = root / "data"; target.write_text("not json")
            link = root / "link"; link.symlink_to(target)
            fifo = root / "fifo"; os.mkfifo(fifo)
            for path in [target, link, fifo, root / "missing"]:
                self.assertEqual(d.snapshot(path, NOW)["status"], "unavailable")
            run.assert_not_called()

    def test_reader_enforces_size_and_complete_valid_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data"; path.write_text(json.dumps(sample()))
            self.assertEqual(d.snapshot(path, NOW)["status"], "fresh")
            with path.open("wb") as f:
                f.truncate(d.MAX_BYTES + 1)
            self.assertEqual(d.snapshot(path, NOW)["status"], "unavailable")

    def test_collector_failure_keeps_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot"; path.write_bytes(b"previous")
            with patch.object(d.subprocess, "run", side_effect=d.subprocess.TimeoutExpired("gh", 60)):
                with self.assertRaises(d.subprocess.SubprocessError):
                    d.collect(path)
            self.assertEqual(path.read_bytes(), b"previous")

    def test_collector_uses_only_read_only_fixed_gh_command(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(d.subprocess, "run") as run:
            run.return_value.stdout = b"[]"
            result = d.collect(Path(tmp) / "snapshot")
            self.assertEqual(result["pull_requests"], 0)
            args = run.call_args.args[0]
            self.assertEqual(args[:5], ["gh", "pr", "list", "--repo", d.REPOSITORY])
            self.assertNotIn("shell", run.call_args.kwargs)


class DecisionHTTPTest(unittest.TestCase):
    def test_routes_remain_host_protected_and_read_only(self):
        import http.client
        import threading
        from tools.control_deck import server
        with patch.object(server, "decision_snapshot", return_value={"status": "unavailable", "rows": []}):
            httpd = server.Server(("127.0.0.1", 0), server.Handler)
            httpd.repo = Path.cwd()
            worker = threading.Thread(target=httpd.serve_forever, daemon=True)
            worker.start()
            host, port = httpd.server_address
            try:
                for method, path, supplied_host, expected in [
                    ("GET", "/decisions", f"127.0.0.1:{port}", 200),
                    ("GET", "/api/decisions", f"127.0.0.1:{port}", 200),
                    ("GET", "/api/command-journal", f"127.0.0.1:{port}", 200),
                    ("GET", "/api/operating-context", f"127.0.0.1:{port}", 200),
                    ("GET", "/api/decisions", "evil.example", 421),
                    ("GET", "/decisions", "evil.example", 421),
                    ("GET", "/api/command-journal", "evil.example", 421),
                    ("GET", "/api/operating-context", "evil.example", 421),
                    ("POST", "/api/decisions", f"127.0.0.1:{port}", 404),
                    ("POST", "/api/decisions", "evil.example", 421),
                    ("POST", "/api/command-journal", f"127.0.0.1:{port}", 404),
                    ("POST", "/api/operating-context", f"127.0.0.1:{port}", 404),
                ]:
                    with self.subTest(method=method, path=path, host=supplied_host):
                        connection = http.client.HTTPConnection(host, port, timeout=3)
                        try:
                            connection.request(method, path, headers={"Host": supplied_host})
                            response = connection.getresponse()
                            self.assertEqual(response.status, expected)
                            response.read()
                        finally:
                            connection.close()
            finally:
                httpd.shutdown(); httpd.server_close(); worker.join(3)


if __name__ == "__main__":
    unittest.main()


class RefusalTest(unittest.TestCase):
    """One test per refusal: each fails if its refusal is removed."""

    def test_timestamp_must_be_text(self):
        with self.assertRaisesRegex(ContractError, "^timestamp$"):
            d._stamp(1792526400)

    def test_timestamp_needs_a_timezone(self):
        with self.assertRaisesRegex(ContractError, "^timestamp timezone$"):
            d._stamp("2026-10-20T10:00:00")

    def test_a_pull_request_must_be_an_object(self):
        raw = sample(); raw["pull_requests"] = ["not a pull request"]
        with self.assertRaisesRegex(ContractError, "^row$"):
            d.normalize(raw, NOW)

    def test_pull_request_number_must_be_a_positive_integer(self):
        for number in ["7", 0, True]:
            raw = sample(); raw["pull_requests"][0]["number"] = number
            with self.assertRaisesRegex(ContractError, "^number$"):
                d.normalize(raw, NOW)

    def test_checks_must_be_a_bounded_list(self):
        for checks in ["contracts", {"name": "contracts"}, [{"name": "c"}] * 301]:
            raw = sample(); raw["pull_requests"][0]["statusCheckRollup"] = checks
            with self.assertRaisesRegex(ContractError, "^checks$"):
                d.normalize(raw, NOW)

    def test_each_check_must_be_an_object(self):
        raw = sample(); raw["pull_requests"][0]["statusCheckRollup"] = ["contracts"]
        with self.assertRaisesRegex(ContractError, "^check$"):
            d.normalize(raw, NOW)

    def test_reviews_must_be_a_list(self):
        raw = sample(); raw["pull_requests"][0]["latestReviews"] = "CHANGES_REQUESTED"
        with self.assertRaisesRegex(ContractError, "^reviews$"):
            d.normalize(raw, NOW)


class SourceBoundTest(unittest.TestCase):
    def write(self, text, tmp):
        path = Path(tmp) / "decisions.json"
        path.write_text(text)
        return path

    def test_a_non_regular_source_is_refused_even_with_valid_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(json.dumps(sample()), tmp)
            with patch.object(d.stat, "S_ISREG", return_value=False):
                self.assertEqual(d.snapshot(path, NOW)["status"], "unavailable")

    def test_source_read_is_bounded_even_when_the_size_report_is_wrong(self):
        # Valid JSON followed by padding: only a read limit can refuse it.
        body = json.dumps(sample()) + " " * (d.MAX_BYTES + 10)
        reported_small = os.stat_result((stat.S_IFREG | 0o600, 0, 0, 1, 0, 0, 10, 0, 0, 0))
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(body, tmp)
            with patch.object(d.os, "fstat", return_value=reported_small):
                self.assertEqual(d.snapshot(path, NOW)["status"], "unavailable")

    def test_github_output_over_the_bound_is_refused_before_it_is_written(self):
        stdout = b" " * 10000 + json.dumps(sample()["pull_requests"], separators=(",", ":")).encode()
        completed = subprocess.CompletedProcess([], 0, stdout=stdout)
        with tempfile.TemporaryDirectory() as tmp, patch.object(d, "MAX_BYTES", 8000), \
                patch.object(d.subprocess, "run", return_value=completed):
            destination = Path(tmp) / "decisions.json"
            with self.assertRaisesRegex(ContractError, "^snapshot size$"):
                d.collect(destination)
            self.assertFalse(destination.exists())

    def test_the_written_snapshot_is_refused_when_it_would_exceed_the_bound(self):
        compact = json.dumps(sample()["pull_requests"], separators=(",", ":")).encode()
        completed = subprocess.CompletedProcess([], 0, stdout=compact)
        with tempfile.TemporaryDirectory() as tmp, patch.object(d, "MAX_BYTES", len(compact)), \
                patch.object(d.subprocess, "run", return_value=completed):
            destination = Path(tmp) / "decisions.json"
            with self.assertRaisesRegex(ContractError, "^snapshot size$"):
                d.collect(destination)
            self.assertFalse(destination.exists())

