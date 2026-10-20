"""Read-only GitHub decision inbox; a snapshot never grants merge authority."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

from geniusnew.contracts import ContractError

REPOSITORY = "Kaancodm/GeniusNew"
MAX_BYTES = 8 * 1024 * 1024
MAX_AGE = 300
FIELDS = ("number,title,url,body,headRefOid,headRefName,isDraft,mergeable,"
          "mergeStateStatus,reviewDecision,statusCheckRollup,latestReviews,updatedAt")


def source_path() -> Path:
    return Path(os.environ.get("GENIUSNEW_DECISIONS_SNAPSHOT",
                "~/.local/state/genius-control-deck/decisions.json")).expanduser()


def _text(value: object, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _stamp(value: object) -> float:
    if not isinstance(value, str):
        raise ContractError("timestamp")
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise ContractError("timestamp timezone")
    return date.timestamp()


def _hints(body: str) -> list[str]:
    # These are excerpts, not a claim that a question remains unanswered.
    section = False
    lines = []
    for line in body.splitlines():
        if line.startswith("#"):
            section = bool(re.search(r"offene (punkte|fragen)|blocker|gates", line, re.I))
            continue
        if line.strip() and (section or "?" in line):
            lines.append(line.strip()[:900])
    return lines[:12]


def _row(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ContractError("row")
    number = raw.get("number")
    if type(number) is not int or number < 1:
        raise ContractError("number")
    sha = raw.get("headRefOid")
    if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise ContractError("head")
    url = f"https://github.com/{REPOSITORY}/pull/{number}"
    if raw.get("url") != url or type(raw.get("isDraft")) is not bool:
        raise ContractError("identity")
    checks = raw.get("statusCheckRollup")
    if not isinstance(checks, list) or len(checks) > 300:
        raise ContractError("checks")
    failed, pending, contracts = [], [], []
    for check in checks:
        if not isinstance(check, dict):
            raise ContractError("check")
        name = _text(check.get("name") or check.get("context"), 120)
        completed = check.get("status") == "COMPLETED" or "state" in check
        state = check.get("conclusion") if "conclusion" in check else check.get("state")
        # Only the repository's own Verify workflow may speak for the contracts gate.
        if name == "contracts" and check.get("workflowName") == "Verify contracts":
            contracts.append(state if completed else "PENDING")
        if state in {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}:
            failed.append(name)
        elif not completed or state not in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
            pending.append(name)
    # Multiple contexts with the same name are ambiguous: do not pick the green one.
    ci = "FAIL" if failed else "PASS" if contracts == ["SUCCESS"] and not pending else "UNKNOWN"
    mergeable = raw.get("mergeable", "UNKNOWN")
    merge_state = raw.get("mergeStateStatus", "UNKNOWN")
    reviews = raw.get("latestReviews", [])
    if not isinstance(reviews, list):
        raise ContractError("reviews")
    request_changes = raw.get("reviewDecision") == "CHANGES_REQUESTED" or any(
        isinstance(r, dict) and r.get("state") == "CHANGES_REQUESTED" for r in reviews)
    if mergeable == "CONFLICTING":
        category, recommendation, target = "blocked", "Branch-Konflikt beheben lassen", url + "/files"
    elif ci == "FAIL":
        category, recommendation, target = "blocked", "Fehlgeschlagene Checks prüfen", url + "/checks"
    elif request_changes:
        category, recommendation, target = "blocked", "Review-Befunde bearbeiten lassen", url + "#pullrequestreview"
    elif raw["isDraft"]:
        category, recommendation, target = "working", "Entwurf und offene Punkte prüfen", url
    elif ci == "PASS" and mergeable == "MERGEABLE" and merge_state == "CLEAN":
        category, recommendation, target = "candidate", "Merge-Freigaben auf GitHub prüfen", url
    else:
        category, recommendation, target = "waiting", "Fehlende Nachweise prüfen", url + "/checks"
    body = _text(raw.get("body"), 65536)
    return {"number": number, "title": _text(raw.get("title"), 300), "url": url,
            "head": sha, "branch": _text(raw.get("headRefName"), 200),
            "draft": raw["isDraft"], "ci": ci, "mergeable": mergeable,
            "merge_state": merge_state, "review_decision": _text(raw.get("reviewDecision"), 50),
            "category": category, "recommendation": recommendation, "action_url": target,
            "failed_checks": failed, "pending_checks": pending, "hints": _hints(body),
            "body": body, "updated_at": _text(raw.get("updatedAt"), 40),
            "conflicts": ["GitHub meldet einen Branch-Konflikt mit der Basis."] if mergeable == "CONFLICTING" else [],
            "review_gate": "UNKNOWN", "merge_authorized": False}


def normalize(raw: object, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    if not isinstance(raw, dict) or raw.get("schema") != 1 or raw.get("repository") != REPOSITORY:
        raise ContractError("snapshot")
    generated = _stamp(raw.get("generated_at"))
    if generated > now + 30:
        raise ContractError("future snapshot")
    prs = raw.get("pull_requests")
    if not isinstance(prs, list) or len(prs) > 100 or raw.get("complete") is not True:
        raise ContractError("incomplete snapshot")
    rows = [_row(row) for row in prs]
    if len({row["number"] for row in rows}) != len(rows):
        raise ContractError("duplicate PR")
    fresh = now - generated <= MAX_AGE
    for row in rows:
        if not fresh:
            row["category"] = "stale"
            row["recommendation"] = "Stand zuerst aktualisieren"
    order = {"candidate": 0, "blocked": 1, "waiting": 2, "working": 3, "stale": 4}
    rows.sort(key=lambda row: (order[row["category"]], -row["number"]))
    return {"status": "fresh" if fresh else "stale", "generated_at": raw["generated_at"],
            "repository": REPOSITORY, "max_age_seconds": MAX_AGE, "rows": rows,
            "coverage": {"github_pull_requests": "COMPLETE", "chat_sessions": "NOT_CONNECTED",
                         "structured_agent_questions": "NOT_CONNECTED"},
            "write_actions": False}


def snapshot(path: Path | None = None, now: float | None = None) -> dict:
    try:
        fd = os.open(path or source_path(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                raise ContractError("source type or size")
            body = stream.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ContractError("source size")
        return normalize(json.loads(body), now)
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return {"status": "unavailable", "rows": [], "write_actions": False,
                "coverage": {"github_pull_requests": "UNKNOWN", "chat_sessions": "NOT_CONNECTED",
                             "structured_agent_questions": "NOT_CONNECTED"}}


def collect(destination: Path) -> dict:
    # Only the separate CLI collector has GitHub access. HTTP GET never starts gh.
    result = subprocess.run(["gh", "pr", "list", "--repo", REPOSITORY,
                             "--state", "open", "--limit", "101", "--json", FIELDS],
                            capture_output=True, timeout=60, check=True)
    if len(result.stdout) > MAX_BYTES:
        raise ContractError("snapshot size")
    rows = json.loads(result.stdout)
    raw = {"schema": 1, "repository": REPOSITORY,
           "generated_at": datetime.now(timezone.utc).isoformat(),
           "complete": isinstance(rows, list) and len(rows) <= 100, "pull_requests": rows}
    normalized = normalize(raw)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = json.dumps(raw, ensure_ascii=False).encode()
    if len(data) > MAX_BYTES:
        raise ContractError("snapshot size")
    fd, name = tempfile.mkstemp(prefix=".decisions-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)
    return {"status": normalized["status"], "pull_requests": len(normalized["rows"]),
            "generated_at": raw["generated_at"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect read-only GitHub decision metadata")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(collect(args.output)))
    except (OSError, ValueError, subprocess.SubprocessError):
        parser.exit(1, "Collection failed; existing snapshot was not refreshed.\n")


if __name__ == "__main__":
    main()
