import unittest

from geniusnew.contracts import ContractError, canonical, issue
from geniusnew.keys import ServiceKeys, derive_keys
from geniusnew.results import WorkerAuthority


class KeyDerivationTest(unittest.TestCase):
    """The separation has to hold by construction, not by remembering."""

    ROOT = b'a-root-secret-of-at-least-32-bytes!!'

    def test_the_three_keys_are_all_different(self):
        keys = derive_keys(self.ROOT)
        self.assertIsInstance(keys, ServiceKeys)
        derived = (keys.integrity_key, keys.result_key, keys.audit_key)
        self.assertEqual(len(set(derived)), 3)
        for key in derived:
            self.assertIs(type(key), bytes)
            self.assertEqual(len(key), 32)

    def test_derivation_is_deterministic(self):
        self.assertEqual(derive_keys(self.ROOT), derive_keys(self.ROOT))

    def test_a_different_root_gives_different_keys(self):
        other = derive_keys(self.ROOT + b'x')
        keys = derive_keys(self.ROOT)
        self.assertNotEqual(keys.integrity_key, other.integrity_key)
        self.assertNotEqual(keys.result_key, other.result_key)
        self.assertNotEqual(keys.audit_key, other.audit_key)

    def test_no_derived_key_can_be_reached_from_another(self):
        """A worker holding the result key must not be able to reach the rest."""
        keys = derive_keys(self.ROOT)
        for known in (keys.result_key, keys.audit_key, keys.integrity_key):
            reachable = derive_keys(known)
            self.assertNotIn(keys.integrity_key,
                             (reachable.integrity_key, reachable.result_key,
                              reachable.audit_key))

    def test_a_short_or_malformed_root_is_refused(self):
        for root in (b'', b'short', b'x' * 31, 'not-bytes', None, 42, bytearray(b'x' * 32)):
            with self.subTest(root=repr(root)[:20]), self.assertRaises(ContractError):
                derive_keys(root)

    def test_the_derived_keys_work_where_they_belong(self):
        keys = derive_keys(self.ROOT)
        authority = WorkerAuthority(result_key=keys.result_key,
                                    integrity_key=keys.integrity_key)
        self.assertEqual(authority.result_key, keys.result_key)

    def test_the_optional_check_still_catches_a_hand_provisioned_reuse(self):
        keys = derive_keys(self.ROOT)
        with self.assertRaises(ContractError):
            WorkerAuthority(result_key=keys.integrity_key,
                            integrity_key=keys.integrity_key)

    def test_the_optional_check_is_documented_as_a_defence_not_an_invariant(self):
        """Omitting it is not checked — which is why derive_keys exists.

        Pinned rather than argued: if this ever starts raising, the docstring
        in results.py claiming it is only a defence has become wrong.
        """
        keys = derive_keys(self.ROOT)
        authority = WorkerAuthority(result_key=keys.integrity_key)
        self.assertEqual(authority.result_key, keys.integrity_key)


if __name__ == '__main__':
    unittest.main()
