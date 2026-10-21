"""The product demo: a real text in, a verified digest out.

The test injects the plain in-process runner through the seam `wiring.build`
documents for tests, so it exercises the whole HTTP/policy/audit path on any
host; that the default path additionally confines the worker is what the
security demo and the isolation suite prove. Published demo secrets only.
"""

import io
import unittest
from contextlib import redirect_stdout

from geniusnew.config import DEMO_ROOT_SECRET
from geniusnew.keys import derive_keys
from geniusnew.results import WorkerAuthority
from geniusnew.workers import WorkerRunner
from scripts.demo_product import API_KEY, SAMPLE, run


class ProductDemoTest(unittest.TestCase):
    def test_the_product_demo_produces_a_verified_digest(self):
        keys = derive_keys(DEMO_ROOT_SECRET)
        authority = WorkerAuthority(result_key=keys.result_key,
                                    integrity_key=keys.integrity_key)
        out = io.StringIO()
        with redirect_stdout(out):
            code = run(runner_factory=lambda worker: WorkerRunner(
                worker, authority=authority))
        printed = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("# Text-Digest", printed)
        self.assertIn("Schlüsselbegriffe", printed)
        self.assertIn("62.137 Records", printed)
        self.assertIn("audit chain verified", printed)
        self.assertNotIn(API_KEY.decode(), printed)
        self.assertNotIn(DEMO_ROOT_SECRET.decode(), printed)
        self.assertLess(len(SAMPLE), 4000)  # the sample stays a sample


if __name__ == "__main__":
    unittest.main()
