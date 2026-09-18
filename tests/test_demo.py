import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.demo import main  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Zero-entropy and self-describing, so no scanner mistakes either for a real
# credential. Their only job is to be distinctive enough that finding one in the
# demo's output is unambiguous.
SECRET_CANARY = "ROOT-SECRET-CANARY-MUST-NOT-BE-PRINTED"
PAYLOAD_CANARY = "PAYLOAD-CANARY-MUST-NOT-BE-PRINTED"


DEFAULT_SECRET = b"a-demo-root-secret-of-32-bytes!!!!!!"
_RUNS: dict[tuple[bytes, str], tuple[int, str]] = {}


def run(root_secret=None, request_text="ordinary demo text"):
    """Run the demo in-process and return its exit code and output.

    Memoized per configuration. The demo is deterministic by construction —
    `test_the_demo_is_deterministic` proves that against two genuinely separate
    runs — so repeating an identical one buys nothing. `scripts/refusals.py`
    runs this whole suite once per refusal, so anything the suite does, it does
    a hundred-odd times.

    Measured, because the first version of this note guessed and was wrong: one
    demo run costs about two milliseconds, and these tests add roughly 0.18s to
    a suite that already takes 1.9s. The memo is a small tidy-up, not a rescue.
    """
    key = (root_secret or DEFAULT_SECRET, request_text)
    if key not in _RUNS:
        captured = io.StringIO()
        with redirect_stdout(captured):
            code = main(root_secret=key[0], request_text=key[1])
        _RUNS[key] = (code, captured.getvalue())
    return _RUNS[key]


def run_uncached(root_secret=None, request_text="ordinary demo text"):
    captured = io.StringIO()
    with redirect_stdout(captured):
        code = main(root_secret=root_secret or DEFAULT_SECRET, request_text=request_text)
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
        self.assertIn("VERIFIED against the anchored head: 3 entries", output)

    def test_every_attack_is_refused(self):
        """The half that matters. A pipeline printing success proves nothing."""
        _, output = run()
        self.assertEqual(output.count("[ok]"), 7, output)
        self.assertNotIn("[!!]", output)
        self.assertIn("7/7 attacks refused", output)

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
            "Replay the result against a different job": "job_id does not match",
            "Accept a result after its handoff expired": "handoff expired before its result",
            "Truncate the audit chain by one entry": "signed head claims",
            "Sign results with the handoff key": "must not be the handoff integrity key",
            "Swap the payload after validation": "payload no longer matches",
            "Dispatch without a gateway permit": "requires a gateway-minted",
            "Reuse the permit for a second dispatch": "already been consumed",
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
        self.assertNotIn("payload no longer matches", second.split("Reuse the permit")[1])

    def test_the_root_secret_never_reaches_the_output(self):
        """Not even a truncated prefix: eight bytes of a key is eight real bytes.

        An earlier version of this walkthrough printed the first sixteen hex
        characters of each derived key to show they differed. Demo output gets
        pasted into issues, so it shows they differ without showing them.
        """
        secret = SECRET_CANARY.encode() + b"-padding-to-thirty-two-bytes"
        code, output = run(root_secret=secret)
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
        code, output = run(request_text=f"please summarise {PAYLOAD_CANARY}")
        self.assertEqual(code, 0)
        self.assertNotIn(PAYLOAD_CANARY, output)

    def test_the_demo_is_deterministic(self):
        """Two genuinely separate runs produce the same digests.

        Uncached on purpose: comparing a memoized result with itself would pass
        whatever the demo did.
        """
        self.assertEqual(run_uncached()[1], run_uncached()[1])

    def test_the_output_carries_the_evidence_not_the_content(self):
        """Every digest printed is 64 hex characters, and there are several."""
        import re
        _, output = run()
        digests = re.findall(r"\b[0-9a-f]{64}\b", output)
        self.assertGreaterEqual(len(digests), 7)
        self.assertIn("artifact digest", output)
        self.assertIn("payload digest", output)
        self.assertIn("output digest", output)

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
