import unittest
from unittest.mock import patch

from argon2 import exceptions

from geniusnew.contracts import ContractError
from geniusnew.portal_passwords import hash_password, verify_password


class PortalPasswordsTest(unittest.TestCase):
    def test_argon2id_round_trip_uses_fresh_salts(self):
        first = hash_password("Grüße🙂")
        second = hash_password("Grüße🙂")
        self.assertTrue(first.startswith("$argon2id$v=19$m=65536,t=3,p=4$"))
        self.assertNotEqual(first, second)
        self.assertIsNone(verify_password(first, "Grüße🙂"))
        with self.assertRaisesRegex(ContractError, "invalid credentials"):
            verify_password(first, "Grusse🙂")

    def test_password_bytes_are_not_normalized_or_trimmed(self):
        stored = hash_password("é ")
        for altered in ("e\u0301 ", "é", " é "):
            with self.subTest(altered=altered), self.assertRaises(ContractError):
                verify_password(stored, altered)

    def test_invalid_password_inputs_are_refused(self):
        stored = hash_password("valid password")
        for password in ("", "x" * 1025, "\ud801", b"valid password", None):
            with self.subTest(password=password), self.assertRaisesRegex(
                    ContractError, "invalid credentials"):
                hash_password(password)
            with self.subTest(password=password), self.assertRaisesRegex(
                    ContractError, "invalid credentials"):
                verify_password(stored, password)

    def test_only_the_approved_argon2id_profile_is_accepted(self):
        stored = hash_password("valid password")
        changes = (stored.replace("argon2id", "argon2i", 1),
                   stored.replace("m=65536", "m=1", 1),
                   stored.replace("t=3", "t=1", 1),
                   stored.replace("p=4", "p=1", 1),
                   stored + "junk", "bad hash", None)
        for changed in changes:
            with self.subTest(changed=changed), self.assertRaisesRegex(
                    ContractError, "invalid credentials"):
                verify_password(changed, "valid password")

    def test_backend_failures_are_closed(self):
        stored = hash_password("valid password")
        with patch("geniusnew.portal_passwords._HASH") as hasher:
            hasher.hash.side_effect = exceptions.HashingError("backend")
            with self.assertRaisesRegex(ContractError, "hashing unavailable"):
                hash_password("valid password")
        with patch("geniusnew.portal_passwords._HASH") as hasher:
            hasher.hash.return_value = "$argon2i$invalid"
            with self.assertRaisesRegex(ContractError, "hashing unavailable"):
                hash_password("valid password")
        with patch("geniusnew.portal_passwords._HASH") as hasher:
            hasher.verify.side_effect = exceptions.VerificationError("backend")
            with self.assertRaisesRegex(ContractError, "invalid credentials"):
                verify_password(stored, "valid password")
        with patch("geniusnew.portal_passwords._HASH") as hasher:
            hasher.verify.return_value = False
            with self.assertRaisesRegex(ContractError, "invalid credentials"):
                verify_password(stored, "valid password")


if __name__ == "__main__":
    unittest.main()
