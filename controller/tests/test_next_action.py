"""Tests for conservative next-action recommendations."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from next_action import load_and_validate, recommend_next_action  # noqa: E402
from validate_state import DEFAULT_SCHEMA, validate_state  # noqa: E402

EXAMPLE = json.loads((ROOT / "state.example.json").read_text(encoding="utf-8"))
SCHEMA = json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))


class NextActionTests(unittest.TestCase):
    def test_every_known_status_has_a_conservative_recommendation(self):
        for status in (
            "new",
            "awaiting_user_decision",
            "awaiting_user_approval",
            "ready_for_user_trigger",
            "blocked",
            "in_progress",
            "paused_usage_reset",
            "blocked_engine_unavailable",
            "blocked_decision_needed",
            "ready_for_chatgpt_review",
            "done",
        ):
            with self.subTest(status=status):
                state = {**EXAMPLE, "status": status}
                if status in {"awaiting_user_decision", "awaiting_user_approval", "ready_for_user_trigger"}:
                    state["decision"] = {
                        "question": "What should happen next?",
                        "options": ["Approve", "Reject"],
                        "recommendation": "Approve the bounded batch",
                        "requested_at": "2026-10-24T07:00:00Z",
                        "decision_record": None
                    }
                self.assertEqual(validate_state(state, SCHEMA), [])
                self.assertTrue(recommend_next_action(state))

    def test_awaiting_decision_never_recommends_progress(self):
        state = {**EXAMPLE, "status": "awaiting_user_decision"}
        message = recommend_next_action(state).lower()
        self.assertIn("stop", message)
        self.assertIn("explicit decision", message)
        self.assertIn("silence", message)

    def test_ready_for_user_trigger_waits_for_user(self):
        state = {**EXAMPLE, "status": "ready_for_user_trigger"}
        self.assertIn("user must explicitly trigger", recommend_next_action(state).lower())

    def test_unavailable_engine_does_not_recommend_fallback(self):
        state = {**EXAMPLE, "status": "blocked_engine_unavailable"}
        message = recommend_next_action(state).lower()
        self.assertIn("remain blocked", message)
        self.assertIn("do not switch", message)

    def test_decision_gate_requires_explicit_approval(self):
        state = {**EXAMPLE, "status": "blocked_decision_needed"}
        message = recommend_next_action(state).lower()
        self.assertIn("explicit", message)
        self.assertIn("silence is not approval", message)

    def test_in_progress_does_not_claim_agent_execution(self):
        message = recommend_next_action(EXAMPLE).lower()
        self.assertIn("no agent launch", message)
        self.assertIn("same engine/model", message)

    def test_checkpoint_outside_repository_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            outside = Path(tmp) / "outside.json"
            outside.write_text(json.dumps(EXAMPLE), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inside the checked-out repository"):
                load_and_validate(outside, root)

    def test_string_checkpoint_path_is_accepted(self):
        state = load_and_validate("controller/state.example.json", Path.cwd())
        self.assertEqual(state["task_id"], EXAMPLE["task_id"])

    def test_invalid_checkpoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state.json").write_text('{"status":"continue"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid checkpoint"):
                load_and_validate(Path("state.json"), root)


if __name__ == "__main__":
    unittest.main()
