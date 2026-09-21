"""The mutation scanner must see the denial metadata it claims to protect."""

import ast
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from scripts import refusals


class RefusalScannerTest(unittest.TestCase):
    def find(self, source):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "gateway.py"
            path.write_text(source)
            with patch.object(refusals, "ROOT", root):
                return refusals.find_refusals(path)

    def test_except_gateway_metadata_is_mutated_to_the_original_refusal(self):
        source = textwrap.dedent('''\
            def admit():
                try:
                    raise ContractError("fixed message")
                except ContractError as refusal:
                    raise GatewayRejected(
                        str(refusal), gateway_id="gateway-test",
                        reason_code="HANDOFF_NOT_VALID", occurred_at=101,
                    ) from None
        ''')
        found = self.find(source)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].line, 5)
        self.assertIn("HANDOFF_NOT_VALID", found[0].message)
        mutated = refusals._disable(source, found[0])
        compile(mutated, "mutant", "exec")

        class ContractError(Exception):
            pass

        class GatewayRejected(ContractError):
            def __init__(self, message, **metadata):
                super().__init__(message)
                self.metadata = metadata

        for code, expected in ((source, GatewayRejected), (mutated, ContractError)):
            namespace = dict(ContractError=ContractError, GatewayRejected=GatewayRejected)
            exec(code, namespace)
            with self.assertRaises(ContractError) as caught:
                namespace["admit"]()
            self.assertIs(type(caught.exception), expected)
            self.assertEqual(str(caught.exception), "fixed message")

    def test_all_except_wrappers_are_discovered_and_mutated_one_at_a_time(self):
        source = textwrap.dedent('''\
            try:
                validate()
            except ContractError as refusal:
                raise GatewayRejected(str(refusal), reason_code="HANDOFF_NOT_VALID")
            try:
                consume()
            except ContractError as refusal:
                raise GatewayRejected(str(refusal), reason_code="APPROVAL_NOT_VALID")
        ''')
        found = self.find(source)
        self.assertEqual([item.line for item in found], [4, 8])
        for refusal in found:
            mutated = refusals._disable(source, refusal)
            raises = [node for node in ast.walk(ast.parse(mutated))
                      if isinstance(node, ast.Raise)]
            self.assertEqual(sum(node.exc is None for node in raises), 1)
            self.assertEqual(sum(isinstance(node.exc, ast.Call) for node in raises), 1)

    def test_original_if_refusal_mutation_still_disables_the_condition(self):
        source = 'if missing:\n    raise GatewayRejected("required")\n'
        found = self.find(source)
        self.assertEqual(len(found), 1)
        self.assertEqual(refusals._disable(source, found[0]),
                         'if False:\n    raise GatewayRejected("required")\n')

    def test_multiline_condition_rewrite_preserves_the_rest_of_the_file(self):
        source = 'if (missing\n        or expired):\n    _fail("no")\nallowed()\n'
        found = self.find(source)
        self.assertEqual(len(found), 1)
        self.assertEqual(refusals._disable(source, found[0]),
                         'if (False):\n    _fail("no")\nallowed()\n')

    def test_unicode_before_an_inline_wrapper_does_not_shift_ast_offsets(self):
        source = ('try:\n    run()\nexcept ContractError: label = "ü"; '
                  'raise GatewayRejected("no")  # keep\n')
        found = self.find(source)
        self.assertEqual(len(found), 1)
        self.assertEqual(refusals._disable(source, found[0]),
                         'try:\n    run()\nexcept ContractError: label = "ü"; raise  # keep\n')

    def test_unrelated_exception_translation_is_not_a_gateway_mutant(self):
        source = 'try:\n    run()\nexcept ValueError:\n    raise RuntimeError("error")\n'
        self.assertEqual(self.find(source), [])


if __name__ == "__main__":
    unittest.main()
