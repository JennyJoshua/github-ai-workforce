#!/usr/bin/env python3
"""Validate an AI workforce checkpoint using the checked-in JSON Schema subset.

Uses only the Python standard library. Unknown fields, missing fields, unknown
statuses, invalid types, and malformed timestamps fail closed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_SCHEMA = Path(__file__).with_name("state.schema.json")


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def _validate(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    errors: list[str] = []

    if "anyOf" in schema:
        alternatives = schema["anyOf"]
        if not any(not _validate(value, option, path) for option in alternatives):
            errors.append(f"{path}: value does not match any allowed type")
        return errors

    expected = schema.get("type")
    if expected and not _matches_type(value, expected):
        return [f"{path}: expected {expected}"]

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is not an allowed option")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: string is too short")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            errors.append(f"{path}: string does not match the required pattern")
        if schema.get("format") == "date-time":
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    errors.append(f"{path}: date-time must include a timezone")
            except ValueError:
                errors.append(f"{path}: invalid date-time")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: required field is missing")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value.keys() - properties.keys():
                errors.append(f"{path}.{key}: unknown field is not allowed")
        for key, child in properties.items():
            if key in value:
                errors.extend(_validate(value[key], child, f"{path}.{key}"))

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: array has fewer than {schema.get('minItems')} items")
        for index, item in enumerate(value):
            errors.extend(_validate(item, schema.get("items", {}), f"{path}[{index}]"))

    return errors


def validate_state(state: Any, schema: dict[str, Any]) -> list[str]:
    """Return validation errors; an empty list means the checkpoint is valid."""
    errors = _validate(state, schema)
    if isinstance(state, dict) and state.get("status") in {
        "awaiting_user_decision", "awaiting_user_approval", "ready_for_user_trigger"
    }:
        decision = state.get("decision")
        if not isinstance(decision, dict):
            errors.append("$.decision: decision details are required while awaiting user input")
        else:
            if not isinstance(decision.get("question"), str) or not decision["question"].strip():
                errors.append("$.decision.question: a specific question is required while awaiting user input")
            if not isinstance(decision.get("options"), list) or not decision["options"]:
                errors.append("$.decision.options: at least one option or explicit response instruction is required")
            if not isinstance(decision.get("requested_at"), str) or not decision["requested_at"].strip():
                errors.append("$.decision.requested_at: request timestamp is required while awaiting user input")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path, help="Path to checkpoint JSON")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    args = parser.parse_args(argv)

    try:
        schema = json.loads(args.schema.read_text(encoding="utf-8"))
        state = json.loads(args.checkpoint.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"INVALID: unable to read valid JSON ({exc})", file=sys.stderr)
        return 2

    errors = validate_state(state, schema)
    if errors:
        print("INVALID checkpoint:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("VALID checkpoint (schema version 1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
