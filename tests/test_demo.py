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


def run(root_secret=None, request_text="ordinary demo text"):
    """Run the demo in-process and return its exit code and output."""
    captured = io.StringIO()
    with redirect_stdout(captured):
        code = main(root_secret=root_secret or b"a-demo-root-secret-of-32-bytes!!!!!!",
                    request_text=request_text)
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
        self.assertEqual(output.count("[ok]"), 5, output)
        self.assertNotIn("[!!]", output)
        self.assertIn("5/5 attacks refused", output)

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
        secret = b"a-demo-root-secret-of-32-bytes!!!!!!"
        _, output = run(root_secret=secret)
        keys = derive_keys(secret)
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
        """Two runs produce the same digests, so a reader can diff them."""
        self.assertEqual(run()[1], run()[1])

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
