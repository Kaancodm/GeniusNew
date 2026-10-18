import hashlib
import json
import unittest

from geniusnew.contracts import ContractError
from geniusnew.d1_codec import (encode_body, encode_unsigned_envelope,
                                parse_body, parse_unsigned_envelope)


class D1CodecTest(unittest.TestCase):
    def setUp(self):
        self.body = b'{"text":"hello"}'
        self.fields = dict(issuer="portal-test", audience="core-test", key_id="key-1",
                           nonce="00" * 32, issued_at=1700000000,
                           expires_at=1700000060)

    def envelope(self, **changes):
        return encode_unsigned_envelope(self.body, **(self.fields | changes))

    def parse(self, wire, body=None, **changes):
        return parse_unsigned_envelope(wire, self.body if body is None else body,
                                       issuer=changes.get("issuer", "portal-test"),
                                       audience=changes.get("audience", "core-test"),
                                       key_id=changes.get("key_id", "key-1"))

    def test_contract_vector_and_round_trip(self):
        wire = self.envelope()
        self.assertEqual(encode_body("hello"), self.body)
        self.assertEqual(parse_body(self.body), "hello")
        self.assertEqual(hashlib.sha256(self.body).hexdigest(),
                         "cbbbdcd27692344de5dbab3abcaba413fb0f45307267de7081401576df1cb176")
        self.assertEqual(hashlib.sha256(
            b"geniusnew/portal-core/request/ed25519/d1\x00" + wire).hexdigest(),
            "bbb318bbf252e9a208bfd24cfe3e5042fe49454cc6fff18829fdf0dace942451")
        self.assertEqual(self.parse(wire)["body_sha256"], hashlib.sha256(self.body).hexdigest())

    def test_unicode_vectors_remain_distinct(self):
        for text, expected in (("Grüße🙂", "c3fc136890181afc0a1556efa7c691fc43647df9681f2d7c6c4a7003704cc87b"),
                               ("é", "d804508ba860f371e3f4ad571e0b760745ca1347987ba8ad41cb3eef8b4c284f"),
                               ("e\u0301", "2169722aee46a09ad91bae5ad6e5eb68e327438cdf02e2e04fe951f97ba466e8")):
            with self.subTest(text=text):
                body = encode_body(text)
                self.assertEqual(hashlib.sha256(body).hexdigest(), expected)
                self.assertEqual(parse_body(body), text)

    def test_body_refusals(self):
        bad = (b'{}', b'{"text":""}', b'{"text":3}', b'{"text":null}',
               b'{"text":"hello","tier":"admin"}', b'{"text":"a","text":"b"}',
               b'{"text":"\\u0068ello"}', b'{"text":"\\ud801"}',
               b'{"text":"hello"} ', b'{"text":"h\xc3\xa9"}', b'[]', b'NaN',
               b'{"text":"x"}' + b' ' * 16384)
        for body in bad:
            with self.subTest(body=body), self.assertRaises(ContractError):
                parse_body(body)
        for text in ("", "\ud801", 1):
            with self.subTest(text=text), self.assertRaises(ContractError):
                encode_body(text)

    def test_distinct_body_boundaries(self):
        with self.assertRaisesRegex(ContractError, "duplicate JSON key"):
            parse_body(b'{"text":"a","text":"b"}')
        with self.assertRaisesRegex(ContractError, "invalid JSON byte length"):
            parse_body(b'{"text":"' + b'a' * 16384 + b'"}')
        with self.assertRaisesRegex(ContractError, "body exceeds D1 limit"):
            encode_body("a" * 16384)

    def test_envelope_refusals(self):
        wire = self.envelope()
        alterations = (
            wire.replace(b'"issuer":"portal-test"', b'"issuer":"other"'),
            wire.replace(b'"version":"geniusnew-portal-core-d1"', b'"version":"other"'),
            wire.replace(b'"path":"/jobs"', b'"path":"/admin"'),
            wire.replace(b'"kind":"REQUEST"', b'"kind":"OTHER"'),
            wire.replace(b'"method":"POST"', b'"method":"GET"'),
            wire.replace(b'"issued_at":1700000000', b'"issued_at":true'),
            wire.replace(b'"issued_at":1700000000', b'"issued_at":1700000000.0'),
            wire.replace(b'"nonce":"' + b'0' * 64 + b'"', b'"nonce":"' + b'A' * 64 + b'"'),
            wire.replace(b'"version":', b'"extra":1,"version":'),
            wire.replace(b'"version":', b'"issuer":"portal-test","version":'),
            wire + b' ', b'{' + wire[1:].replace(b'"audience"', b'"\\u0061udience"', 1),
            wire + b' ' * 4096,
        )
        for altered in alterations:
            with self.subTest(altered=altered), self.assertRaises(ContractError):
                self.parse(altered)
        with self.assertRaises(ContractError):
            self.parse(wire, body=b'{"text":"other"}')
        for changes in (dict(issued_at=1700000060), dict(expires_at=1700000061),
                        dict(issued_at=-1), dict(expires_at=4102444800),
                        dict(issued_at=True), dict(issuer="bad name"),
                        dict(nonce="0" * 63)):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                self.envelope(**changes)
        for key in ("issuer", "audience", "key_id"):
            with self.subTest(key=key), self.assertRaises(ContractError):
                self.parse(wire, **{key: "other"})

    def test_distinct_envelope_boundaries(self):
        wire = self.envelope()
        extra = json.loads(wire)
        extra["extra"] = "value"
        with self.assertRaisesRegex(ContractError, "wrong fields"):
            self.parse(json.dumps(extra, ensure_ascii=True, sort_keys=True,
                                  separators=(",", ":")).encode("ascii"))
        with self.assertRaisesRegex(ContractError, "body digest must be"):
            self.parse(wire.replace(b'"body_sha256":"', b'"body_sha256":"X', 1))
        with self.assertRaisesRegex(ContractError, "invalid JSON byte length"):
            self.parse(wire + b' ' * 4096)


if __name__ == "__main__":
    unittest.main()
