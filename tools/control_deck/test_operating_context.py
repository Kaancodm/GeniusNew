from datetime import datetime, timezone
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from tools.control_deck.operating_context import snapshot, require_current


class ContextTest(unittest.TestCase):
    def data(self):
        return {"schema": 1, "revision": "user-20261020-v1", "updated_at": datetime.now(timezone.utc).isoformat(),
                "rules": [{"id": "review", "text": "Gemini performs general PR reviews", "source": "user instruction"}],
                "tools": [{"name": "Copilot", "state": "exhausted", "detail": "No verified reset", "source": "user instruction"}],
                "acknowledgements": []}

    def read(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"context.json"; path.write_text(json.dumps(data))
            return snapshot(path)

    def test_saving_rules_is_not_an_agent_acknowledgement(self):
        result=self.read(self.data())
        self.assertEqual(result["status"], "ready")
        self.assertTrue(all(a["status"] == "NOT_CONFIRMED" for a in result["agents"]))

    def test_old_revision_receipt_is_not_current(self):
        data=self.data(); data["acknowledgements"]=[{"agent":"Claude","revision":"old","verified":True}]
        self.assertEqual(self.read(data)["agents"][1]["status"], "NOT_CONFIRMED")

    def test_manual_verified_claim_is_never_a_receipt(self):
        data=self.data(); digest=self.read(data)["context_sha256"]
        data["acknowledgements"]=[{"agent":"Claude","revision":"user-20261020-v1","context_sha256":digest,"verified":True}]
        agent=self.read(data)["agents"][1]
        self.assertEqual(agent["status"], "MANUAL_UNAUTHENTICATED")
        self.assertNotIn("RECEIPT", agent["status"])

    def test_static_bulletin_cannot_mark_provider_available(self):
        data=self.data(); data["tools"][0]["state"]="available"
        self.assertEqual(self.read(data)["status"], "unavailable")

    def test_exhausted_state_never_automatically_resets(self):
        self.assertEqual(self.read(self.data())["tools"][0]["state"], "exhausted")

    def test_invalid_context_does_not_invent_rules(self):
        result=self.read({})
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["rules"], [])

    def test_changed_content_changes_digest_even_if_revision_label_is_reused(self):
        data=self.data(); before=self.read(data)["context_sha256"]
        data["rules"][0]["text"]="New user decision"
        self.assertNotEqual(before,self.read(data)["context_sha256"])

    def test_a_non_regular_file_is_refused_even_when_its_content_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "context.json"; path.write_text(json.dumps(self.data()))
            with patch.object(stat, "S_ISREG", return_value=False):
                self.assertEqual(snapshot(path)["status"], "unavailable")

    def test_oversized_context_is_refused_even_when_it_is_valid_json(self):
        # Valid JSON plus whitespace: only the read bound can refuse it.
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"context.json"
            path.write_text(json.dumps(self.data())+" "*70000)
            self.assertEqual(snapshot(path)["status"], "unavailable")

    def test_wrong_schema_version_is_refused(self):
        data=self.data(); data["schema"]=2
        self.assertEqual(self.read(data)["status"], "unavailable")

    def test_revision_label_must_be_a_plain_token(self):
        data=self.data(); data["revision"]="user rev!"
        self.assertEqual(self.read(data)["status"], "unavailable")

    def test_naive_timestamp_is_refused(self):
        data=self.data(); data["updated_at"]=datetime.now().replace(tzinfo=None).isoformat()
        self.assertEqual(self.read(data)["status"], "unavailable")

    def test_future_timestamp_is_refused(self):
        data=self.data()
        data["updated_at"]=datetime.fromtimestamp(datetime.now(timezone.utc).timestamp()+3600, timezone.utc).isoformat()
        self.assertEqual(self.read(data)["status"], "unavailable")

    def test_more_than_fifty_rules_is_refused(self):
        data=self.data(); data["rules"]=[data["rules"][0]]*51
        self.assertEqual(self.read(data)["status"], "unavailable")

    def test_empty_rule_text_is_refused(self):
        data=self.data(); data["rules"][0]["text"]=""
        self.assertEqual(self.read(data)["status"], "unavailable")

    def test_overlong_tool_detail_is_refused(self):
        data=self.data(); data["tools"][0]["detail"]="x"*501
        self.assertEqual(self.read(data)["status"], "unavailable")

    def test_handoff_gate_requires_same_assignment_receipt_and_current_digest(self):
        context=self.read(self.data()); digest=context["context_sha256"]
        require_current(context,digest,digest)
        for assignment,receipt in [("a"*64,digest),(digest,"b"*64),(digest,"")]:
            with self.assertRaises(ValueError):
                require_current(context,assignment,receipt)
        with self.assertRaisesRegex(ValueError, "CONTEXT_CHANGED_OR_NOT_ACKNOWLEDGED"):
            require_current(context,"a"*64,digest)
        with self.assertRaisesRegex(ValueError, "CONTEXT_UNAVAILABLE"):
            require_current({"status":"unavailable"},digest,digest)
        with self.assertRaisesRegex(ValueError, "CONTEXT_UNAVAILABLE"):
            require_current({"status":"ready","context_sha256":"not-a-digest"},digest,digest)


if __name__ == "__main__":
    unittest.main()
