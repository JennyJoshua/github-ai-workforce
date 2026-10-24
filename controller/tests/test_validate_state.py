"""Unit tests for the checkpoint validator; standard library only."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from validate_state import validate_state  # noqa: E402

SCHEMA = json.loads((ROOT / "state.schema.json").read_text(encoding="utf-8"))
EXAMPLE = json.loads((ROOT / "state.example.json").read_text(encoding="utf-8"))


class CheckpointValidationTests(unittest.TestCase):
    def test_example_checkpoint_is_valid(self):
        self.assertEqual(validate_state(EXAMPLE, SCHEMA), [])

    def test_missing_required_field_fails_closed(self):
        state = dict(EXAMPLE)
        del state["required_engine"]
        errors = validate_state(state, SCHEMA)
        self.assertTrue(any("$.required_engine: required field is missing" in e for e in errors))

    def test_unknown_status_is_rejected(self):
        state = {**EXAMPLE, "status": "continue_with_fallback"}
        self.assertTrue(any("$.status: value is not an allowed option" in e for e in validate_state(state, SCHEMA)))

    def test_unknown_field_is_rejected(self):
        state = {**EXAMPLE, "auto_approve": True}
        self.assertTrue(any("$.auto_approve: unknown field is not allowed" in e for e in validate_state(state, SCHEMA)))

    def test_unknown_provider_is_rejected(self):
        state = {**EXAMPLE, "required_engine": {"provider": "alternate", "model": "unknown"}}
        self.assertTrue(any("$.required_engine.provider" in e for e in validate_state(state, SCHEMA)))

    def test_blank_model_is_rejected(self):
        state = {**EXAMPLE, "required_engine": {"provider": "claude", "model": ""}}
        self.assertTrue(any("$.required_engine.model: string is too short" in e for e in validate_state(state, SCHEMA)))

    def test_commit_sha_must_be_full_hex_sha_or_null(self):
        state = {**EXAMPLE, "commit_sha": "abc123"}
        self.assertTrue(any("$.commit_sha: value does not match any allowed type" in e for e in validate_state(state, SCHEMA)))

    def test_naive_timestamp_is_rejected(self):
        state = {**EXAMPLE, "updated_at": "2026-10-24T00:00:00"}
        self.assertTrue(any("must include a timezone" in e for e in validate_state(state, SCHEMA)))

    def test_invalid_check_result_is_rejected(self):
        state = {**EXAMPLE, "checks": [{"name": "test", "result": "green", "evidence": "run"}]}
        self.assertTrue(any("$.checks[0].result" in e for e in validate_state(state, SCHEMA)))

    def test_boolean_is_not_accepted_as_integer_version(self):
        state = {**EXAMPLE, "schema_version": True}
        self.assertTrue(any("$.schema_version: expected integer" in e for e in validate_state(state, SCHEMA)))

    def test_awaiting_decision_requires_question_options_and_timestamp(self):
        state = {**EXAMPLE, "status": "awaiting_user_decision"}
        errors = validate_state(state, SCHEMA)
        self.assertTrue(any("$.decision.question" in e for e in errors))
        self.assertTrue(any("$.decision.options" in e for e in errors))
        self.assertTrue(any("$.decision.requested_at" in e for e in errors))

    def test_awaiting_decision_with_explicit_prompt_is_valid(self):
        state = {**EXAMPLE, "status": "awaiting_user_decision", "decision": {
            "question": "Approve the proposed next batch?",
            "options": ["Approve", "Reject"],
            "recommendation": "Approve the bounded test-only batch",
            "requested_at": "2026-10-24T07:00:00Z",
            "decision_record": None
        }}
        self.assertEqual(validate_state(state, SCHEMA), [])


if __name__ == "__main__":
    unittest.main()
