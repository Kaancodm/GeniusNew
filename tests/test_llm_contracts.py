import json
import unittest
from unittest.mock import patch

from geniusnew.contracts import ContractError, canonical
from geniusnew.llm_contracts import (
    BrokerRequest,
    BrokerResponse,
    Usage,
    decode_request,
    decode_response,
)


JOB = "job-123"
DIGEST = "a" * 64


class BrokerContractTest(unittest.TestCase):
    def request(self):
        return BrokerRequest(JOB, DIGEST, 200, "Ein kurzer deutscher Text.")

    def response(self):
        return BrokerResponse(JOB, DIGEST, "Kurze Zusammenfassung.", Usage(8, 5))

    def test_request_round_trip_and_deadline(self):
        request = self.request()
        self.assertEqual(decode_request(request.to_bytes(), now=100), request)
        with self.assertRaises(ContractError):
            decode_request(request.to_bytes(), now=200)
        with self.assertRaises(ContractError):
            decode_request(BrokerRequest(JOB, DIGEST, 401, "Text.").to_bytes(), now=100)

    def test_request_rejects_unknown_fields_wrong_types_and_oversize(self):
        value = json.loads(self.request().to_bytes())
        for bad in (
            {**value, "provider_url": "https://example.invalid"},
            {**value, "job_id": True},
            {**value, "job_id": "j" * 129},
            {**value, "expires_at": True},
            {**value, "version": "1"},
            {**value, "text": ""},
            {**value, "handoff_sha256": "b"},
        ):
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                decode_request(canonical(bad), now=100)
        with self.assertRaises(ContractError):
            decode_request(canonical({**value, "text": "x" * 32768}), now=100)
        with self.assertRaises(ContractError):
            decode_request(canonical({**value, "text": "x" * (8 * 1024 + 1)}), now=100)

    def test_request_rejects_wire_size_independently_of_text_limit(self):
        value = json.loads(self.request().to_bytes())
        with patch("geniusnew.llm_contracts._MAX_TEXT_BYTES", 64 * 1024):
            oversized = canonical({**value, "text": "x" * (32 * 1024)})
            with self.assertRaises(ContractError):
                decode_request(oversized, now=100)

            request = BrokerRequest(JOB, DIGEST, 200, "x" * (32 * 1024))
            with self.assertRaises(ContractError):
                request.to_bytes()

    def test_request_rejects_noncanonical_and_duplicate_keys(self):
        value = json.loads(self.request().to_bytes())
        with self.assertRaises(ContractError):
            decode_request(json.dumps(value).encode(), now=100)
        duplicate = self.request().to_bytes().replace(b'"job_id":', b'"job_id":"other","job_id":')
        with self.assertRaises(ContractError):
            decode_request(duplicate, now=100)
        with self.assertRaises(ContractError):
            decode_request(bytearray(self.request().to_bytes()), now=100)

    def test_invalid_unicode_is_refused_by_the_contract(self):
        for job_id, text, message in (("\ud800", "Text.", "job_id must be valid UTF-8"),
                                      (JOB, "\ud800", "text must be valid UTF-8")):
            with self.subTest(field=message), self.assertRaisesRegex(ContractError, message):
                BrokerRequest(job_id, DIGEST, 200, text)

    def test_invalid_json_and_ascii_are_contract_refusals(self):
        for wire in (b"{", b"\xff"):
            with self.subTest(wire=wire[:20]), self.assertRaisesRegex(
                    ContractError, "message is invalid JSON"):
                decode_request(wire, now=100)
        with patch("geniusnew.llm_contracts.json.loads", side_effect=RecursionError):
            with self.assertRaisesRegex(ContractError, "message is invalid JSON"):
                decode_request(self.request().to_bytes(), now=100)

    def test_nonfinite_json_is_not_canonical(self):
        wire = self.request().to_bytes().replace(b'"expires_at":200',
                                               b'"expires_at":NaN')
        with self.assertRaisesRegex(ContractError, "message is not canonical JSON"):
            decode_request(wire, now=100)

    def test_request_constructor_rejects_invalid_deadline_types_and_bounds(self):
        for expires_at in (True, 0, 4_102_444_801):
            with self.subTest(expires_at=expires_at), self.assertRaises(ContractError):
                BrokerRequest(JOB, DIGEST, expires_at, "Text.")

    def test_request_decoder_requires_an_exact_integer_clock(self):
        with self.assertRaises(ContractError):
            decode_request(self.request().to_bytes(), now=True)

    def test_response_requires_exact_job_binding(self):
        response = self.response()
        self.assertEqual(decode_response(response.to_bytes(), job_id=JOB,
                                         handoff_sha256=DIGEST), response)
        for job_id, digest in (("other", DIGEST), (JOB, "b" * 64)):
            with self.subTest(job_id=job_id), self.assertRaises(ContractError):
                decode_response(response.to_bytes(), job_id=job_id,
                                handoff_sha256=digest)

    def test_response_rejects_unknown_fields_wrong_types_and_oversize(self):
        value = json.loads(self.response().to_bytes())
        for bad in (
            {**value, "tool_call": {"name": "shell"}},
            {**value, "version": "1"},
            {**value, "text": []},
            {**value, "text": ""},
            {**value, "usage": {"input_tokens": True, "output_tokens": 5}},
            {**value, "usage": {"input_tokens": 8, "output_tokens": -1}},
            {**value, "usage": {"input_tokens": 8, "output_tokens": 5,
                                 "cost": "unknown"}},
        ):
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                decode_response(canonical(bad), job_id=JOB, handoff_sha256=DIGEST)
        with self.assertRaises(ContractError):
            decode_response(canonical({**value, "text": "x" * 32768}),
                            job_id=JOB, handoff_sha256=DIGEST)

    def test_response_rejects_wire_size_independently_of_text_limit(self):
        with patch("geniusnew.llm_contracts._MAX_TEXT_BYTES", 64 * 1024):
            response = BrokerResponse(JOB, DIGEST, "x" * (32 * 1024), None)
            with self.assertRaises(ContractError):
                response.to_bytes()

    def test_response_constructor_rejects_untyped_usage(self):
        with self.assertRaises(ContractError):
            BrokerResponse(JOB, DIGEST, "Kurz.", {"input_tokens": 1})

    def test_missing_usage_is_explicitly_unknown(self):
        response = BrokerResponse(JOB, DIGEST, "Kurz.", None)
        self.assertIsNone(decode_response(response.to_bytes(), job_id=JOB,
                                          handoff_sha256=DIGEST).usage)

    def test_multibyte_text_stays_inside_the_frame_limit(self):
        request = BrokerRequest(JOB, DIGEST, 200, "😀" * 2048)
        self.assertLessEqual(len(request.to_bytes()), 32 * 1024)

    def test_repr_does_not_expose_text(self):
        self.assertNotIn("Ein kurzer deutscher Text.", repr(self.request()))
        self.assertNotIn("Kurze Zusammenfassung.", repr(self.response()))


if __name__ == "__main__":
    unittest.main()
