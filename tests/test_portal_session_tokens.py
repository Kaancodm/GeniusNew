import hashlib
import re
import unittest
from unittest.mock import patch

from geniusnew.contracts import ContractError
from geniusnew.portal_session_tokens import (
    clear_session_cookie, csrf_token, new_session_token, session_cookie,
    session_digest, verify_csrf_token,
)


class PortalSessionTokensTest(unittest.TestCase):
    def test_random_session_token_and_storage_digest(self):
        first = new_session_token()
        second = new_session_token()
        self.assertRegex(first, re.compile(r"[A-Za-z0-9_-]{43}\Z"))
        self.assertNotEqual(first, second)
        self.assertNotEqual(session_digest(first), session_digest(second))
        with patch("geniusnew.portal_session_tokens.secrets.token_bytes",
                   return_value=b"\x01" * 32):
            fixed = new_session_token()
        self.assertEqual(session_digest(fixed), hashlib.sha256(b"\x01" * 32).hexdigest())

    def test_malformed_and_noncanonical_tokens_are_refused(self):
        with patch("geniusnew.portal_session_tokens.secrets.token_bytes",
                   return_value=b"\x00" * 32):
            valid = new_session_token()
        for malformed in ("", valid + "=", valid + "\n", valid[:-1] + "+",
                          valid[:-1], valid[:-1] + "B", None, b"raw"):
            with self.subTest(malformed=malformed), self.assertRaises(ContractError):
                session_digest(malformed)
            with self.subTest(malformed=malformed), self.assertRaises(ContractError):
                session_cookie(malformed)

    def test_entropy_failure_is_refused(self):
        with patch("geniusnew.portal_session_tokens.secrets.token_bytes",
                   side_effect=OSError("rng")):
            with self.assertRaisesRegex(ContractError, "entropy unavailable"):
                new_session_token()
        with patch("geniusnew.portal_session_tokens.secrets.token_bytes",
                   return_value=b"short"):
            with self.assertRaisesRegex(ContractError, "entropy unavailable"):
                new_session_token()

    def test_csrf_is_bound_to_session_and_server_key(self):
        first, second = new_session_token(), new_session_token()
        key, other_key = b"a" * 32, b"b" * 32
        token = csrf_token(first, key)
        self.assertIsNone(verify_csrf_token(first, key, token))
        for session, secret, submitted in (
            (second, key, token), (first, other_key, token),
            (first, key, csrf_token(second, key)), (first, key, "missing"),
        ):
            with self.subTest(session=session, secret=secret, submitted=submitted):
                with self.assertRaises(ContractError):
                    verify_csrf_token(session, secret, submitted)
        for bad_key in (b"short", "a" * 32, None):
            with self.subTest(bad_key=bad_key), self.assertRaisesRegex(
                    ContractError, "invalid Portal CSRF key"):
                csrf_token(first, bad_key)

    def test_cookie_is_host_only_secure_and_cleared_at_same_scope(self):
        token = new_session_token()
        cookie = session_cookie(token)
        cleared = clear_session_cookie()
        self.assertEqual(cookie,
                         "__Host-geniusnew_session=" + token +
                         "; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=28800")
        self.assertEqual(cleared,
                         "__Host-geniusnew_session=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0")
        self.assertNotIn("Domain=", cookie)


if __name__ == "__main__":
    unittest.main()
