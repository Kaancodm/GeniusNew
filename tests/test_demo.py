import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import demo  # noqa: E402
from scripts.demo import API_KEY, main  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Zero-entropy and self-describing, so no scanner mistakes either for a real
# credential. Their only job is to be distinctive enough that finding one in the
# demo's output is unambiguous.
SECRET_CANARY = "ROOT-SECRET-CANARY-MUST-NOT-BE-PRINTED"
PAYLOAD_CANARY = "PAYLOAD-CANARY-MUST-NOT-BE-PRINTED"
API_KEY_CANARY = b"API-KEY-CANARY-MUST-NOT-BE-PRINTED!!"


DEFAULT_SECRET = b"a-demo-root-secret-of-32-bytes!!!!!!"
_RUNS: dict[tuple[bytes, str], tuple[int, str]] = {}


def run(root_secret=None, request_text="ordinary demo text", api_key=None):
    """Run the demo in-process and return its exit code and output.

    Memoized per configuration. The demo is deterministic by construction —
    `test_the_demo_is_deterministic` proves that against two genuinely separate
    runs — so repeating an identical one buys nothing. `scripts/refusals.py`
    runs this whole suite once per refusal, so anything the suite does, it does
    a hundred-odd times.

    Measured, and measured again when it changed: a run used to cost about two
    milliseconds, and since the worker runs in its own process and the anchor
    in another it costs about a quarter of a second. At that price the memo
    matters, and so does how many distinct runs the tests ask for.
    """
    key = (root_secret or DEFAULT_SECRET, request_text, api_key or API_KEY)
    if key not in _RUNS:
        captured = io.StringIO()
        with redirect_stdout(captured):
            code = main(root_secret=key[0], request_text=key[1], api_key=key[2])
        _RUNS[key] = (code, captured.getvalue())
    return _RUNS[key]


def run_with_canaries():
    """One run carrying all three canaries at once.

    Each leak test still asserts its own canary is absent; they share a run
    because a leak of one does not depend on the others being ordinary, and
    three separate runs were three quarters of a second of every suite run.
    """
    return run(root_secret=SECRET_CANARY.encode() + b"-padding-to-thirty-two-bytes",
               request_text=f"please summarise {PAYLOAD_CANARY}",
               api_key=API_KEY_CANARY)


def run_uncached(root_secret=None, request_text="ordinary demo text", api_key=None):
    captured = io.StringIO()
    with redirect_stdout(captured):
        code = main(root_secret=root_secret or DEFAULT_SECRET,
                    request_text=request_text, api_key=api_key or API_KEY)
    return code, captured.getvalue()


class DemoTest(unittest.TestCase):
    """`scripts/demo.sh` is the one command roadmap step 18 asks for.

    These tests are also what makes it a check rather than a printout: a demo
    nobody runs in CI rots, and a demo that cannot fail proves nothing.
    """

    def test_the_demo_passes_and_says_so(self):
        code, output = run()
        self.assertEqual(code, 0, output)
        self.assertIn("PASS", output)
        self.assertIn("VERIFIED against the anchored head: 4 entries", output)
        self.assertIn("anchor runs in its own process: True", output)

    def test_every_attack_is_refused(self):
        """The half that matters. A pipeline printing success proves nothing."""
        _, output = run()
        self.assertEqual(output.count("[ok]"), 16, output)
        self.assertNotIn("[!!]", output)
        self.assertIn("16/16 attacks refused", output)

    def test_each_attack_is_refused_by_the_check_it_targets(self):
        """Refused is not enough — it has to be refused by the right check.

        The permit-reuse attack first "passed" because the payload-swap attack
        before it left the handoff mutated, so acceptance failed on the digest
        instead of on the permit. That is the failure mode this repository keeps
        hitting: an assertion satisfied by a path other than the one under test.
        Matching the message is what distinguishes them.
        """
        _, output = run()
        expected = {
            "Submit with a key that is not registered": "401 UNAUTHENTICATED",
            "Claim a tier in the request body": "400 MALFORMED_REQUEST",
            "Choose your own job id": "400 MALFORMED_REQUEST",
            "Knock on a door that is not there": "404 NOT_FOUND",
            "Replay the result against a different job": "job_id does not match",
            "Accept a result after its handoff expired": "not currently valid",
            "Accept the same result twice": "already has an accepted result",
            "Sign results with the handoff key": "must not be the handoff integrity key",
            "Mint a handoff with the gateway's key": "signer must be a HandoffSigner",
            "Sign a result with the verifier's key": "authority must be a WorkerAuthority",
            "Swap the payload after validation": "payload no longer matches",
            "Dispatch without a gateway permit": "requires a gateway-minted",
            "Truncate the chain, keep the old head": "signed head claims",
            "Truncate the chain and re-sign the head": "anchor committed",
            "Rewind the anchor from inside the writer": "anchor committed",
            "Restart the service and re-sign the chain": "anchor committed",
        }
        import re
        refusals = dict(re.findall(r"\[ok\] (.+?)\s{2,}(.+)", output))
        self.assertEqual(set(refusals), set(expected), "attack list changed")
        for attack, fragment in expected.items():
            with self.subTest(attack=attack):
                self.assertIn(fragment, refusals[attack])

    def test_an_attack_leaves_no_state_behind_for_the_next_one(self):
        """The payload-swap attack must put the payload back, or it poisons the rest."""
        _, second = run_uncached()
        self.assertNotIn("payload no longer matches",
                         second.split("Dispatch without a gateway permit")[1])

    def test_a_failed_job_cannot_print_pass(self):
        """The chain and the attacks can all be fine while the job failed.

        A worker that raises still yields a valid signed FAILED result, a
        verifiable chain and eight correctly refused attacks. Checking only
        those printed PASS. One lone surrogate in the request is enough: the
        summarizer raises while encoding it.
        """
        code, output = run_uncached(request_text="\ud800")
        self.assertIn("status FAILED", output)
        self.assertIn("FAIL", output)
        self.assertNotIn("PASS", output)
        self.assertEqual(code, 1)

    def test_the_anchor_attack_is_the_one_the_anchor_is_for(self):
        """Truncating and re-signing is refused by the anchor, not by the head.

        Handing the original head to a shortened chain is refused by the count
        inside that head — it would still be refused with the anchor deleted, so
        as evidence for the anchor it proves nothing. The re-signed variant is
        the one that needs it.
        """
        _, output = run()
        import re
        refusals = dict(re.findall(r"\[ok\] (.+?)\s{2,}(.+)", output))
        self.assertIn("signed head claims",
                      refusals["Truncate the chain, keep the old head"])
        self.assertIn("anchor committed",
                      refusals["Truncate the chain and re-sign the head"])

    def test_the_root_secret_never_reaches_the_output(self):
        """Not even a truncated prefix: eight bytes of a key is eight real bytes.

        An earlier version of this walkthrough printed the first sixteen hex
        characters of each derived key to show they differed. Demo output gets
        pasted into issues, so it shows they differ without showing them.
        """
        secret = SECRET_CANARY.encode() + b"-padding-to-thirty-two-bytes"
        code, output = run_with_canaries()
        self.assertEqual(code, 0)
        self.assertNotIn(SECRET_CANARY, output)
        for fragment in (secret.hex(), secret.hex()[:16], SECRET_CANARY[:12]):
            self.assertNotIn(fragment, output)

    def test_no_derived_key_material_reaches_the_output(self):
        from geniusnew.keys import derive_keys
        _, output = run()
        keys = derive_keys(DEFAULT_SECRET)
        for key in (keys.integrity_key, keys.result_key, keys.audit_key):
            with self.subTest(key=key.hex()[:8]):
                self.assertNotIn(key.hex(), output)
                self.assertNotIn(key.hex()[:16], output)

    def test_the_payload_never_reaches_the_output(self):
        """Roadmap step 18: the output contains only what step 7 permits.

        Step 7 is the audit event contract, which forbids raw payloads. The
        digest stands for the payload, and the digest is the part that proves
        nobody swapped it.
        """
        code, output = run_with_canaries()
        self.assertEqual(code, 0)
        self.assertNotIn(PAYLOAD_CANARY, output)

    def test_the_api_key_never_reaches_the_output(self):
        """It is a credential a stranger presents, and the demo prints requests."""
        code, output = run_with_canaries()
        self.assertEqual(code, 0)
        for fragment in (API_KEY_CANARY.decode(), API_KEY_CANARY.decode()[:12],
                         API_KEY_CANARY.hex()):
            with self.subTest(fragment=fragment[:20]):
                self.assertNotIn(fragment, output)

    def test_the_job_really_went_in_over_http(self):
        """The goal statement asks for HTTP, not for a convincing arrangement."""
        _, output = run()
        self.assertIn("HTTP entrance listening on 127.0.0.1:", output)
        self.assertIn("Job submitted — HTTP 202", output)
        self.assertIn("identity came from the key, server-side", output)
        self.assertIn("[ok] Submit with a key that is not registered", output)

    def test_the_chain_names_all_security_roles_on_the_success_path(self):
        """Issuance, gateway admission and result acceptance name their actors."""
        _, output = run()
        self.assertIn("orchestrator HANDOFF_ISSUED", output)
        self.assertIn("gateway      HANDOFF_ADMITTED", output)
        self.assertIn("orchestrator EXECUTION_DISPATCHED", output)
        self.assertIn("monitor      RESULT_ACCEPTED", output)

    def test_the_demo_is_deterministic(self):
        """Two genuinely separate runs produce the same digests.

        One side is always fresh: comparing a memoized result with itself would
        pass whatever the demo did. The other may come from the memo, which is
        itself the output of an earlier, separate run. The listening port is
        masked because the kernel picks it — everything a reader would diff is
        a digest, and those are fixed by the fixed clock and the fixed job ids.
        """
        import re

        def stable(output):
            return re.sub(r"127\.0\.0\.1:\d+", "127.0.0.1:PORT", output)

        self.assertEqual(stable(run()[1]), stable(run_uncached()[1]))

    def test_the_output_carries_the_evidence_not_the_content(self):
        """Every digest printed is 64 hex characters, and there are several."""
        import re
        _, output = run()
        digests = re.findall(r"\b[0-9a-f]{64}\b", output)
        self.assertGreaterEqual(len(digests), 6)
        self.assertIn("handoff digest", output)
        self.assertIn("result digest", output)

    def test_a_host_without_posix_isolation_gets_a_fail_that_says_where_to_run(self):
        """Windows: the service refuses to run (fail closed), and the demo says why.

        A traceback would also be a non-zero exit, but a stranger reads it as
        a broken repository rather than as a refusal with a way round it.
        """
        from unittest.mock import patch
        with patch.object(demo, "_resource_supported", return_value=False):
            code, output = run_uncached()
        self.assertEqual(code, 1)
        self.assertRegex(output, r"\AFAIL — .*fail closed.*WSL")
        self.assertNotIn("PASS", output)
        self.assertNotIn("[0]", output)

    def test_the_readme_quotes_the_line_the_demo_really_ends_with(self):
        """Roadmap step 20: a stranger compares their last line with the README's.

        The count in that line changes whenever an attack is added, and the
        README has to change with it. A sentence in a document does not notice
        when it did not.
        """
        import re
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        quoted = re.findall(r"^PASS — .+$", readme, flags=re.MULTILINE)
        _, output = run()
        self.assertEqual(quoted, [output.rstrip("\n").splitlines()[-1]])

    def test_the_shell_entry_point_runs_it(self):
        """The command a stranger actually types."""
        script = ROOT / "scripts" / "demo.sh"
        self.assertTrue(script.exists())
        completed = subprocess.run([str(script)], capture_output=True, text=True,
                                   cwd=ROOT, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("PASS", completed.stdout)

    def test_the_shell_entry_point_runs_from_any_directory(self):
        """A stranger may call it by path from anywhere; `cd` must not be needed."""
        completed = subprocess.run([str(ROOT / "scripts" / "demo.sh")],
                                   capture_output=True, text=True, cwd="/tmp", timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("PASS", completed.stdout)


if __name__ == "__main__":
    unittest.main()
