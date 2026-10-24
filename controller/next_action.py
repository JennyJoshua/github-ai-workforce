"""Recommend the next safe manual action for a validated checkpoint.

This tool deliberately does not invoke ChatGPT, Claude, Notion, or any provider
API. Availability and approval are never inferred from status alone.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from validate_state import DEFAULT_SCHEMA, validate_state

ACTIONS = {
    "awaiting_user_decision": (
        "STOP: Present the recorded question and options to the user. Wait for an explicit "
        "decision to be recorded; do not proceed because of silence or a page view."
    ),
    "awaiting_user_approval": (
        "STOP: Obtain and record the user's explicit approval or rejection before continuing."
    ),
    "ready_for_user_trigger": (
        "WAIT: The user must explicitly trigger the next step. Do not start work automatically."
    ),
    "blocked": (
        "REMAIN BLOCKED: Inspect the recorded blocker and request a decision or fix. "
        "Do not guess or bypass the blocker."
    ),
    "new": (
        "STOP: Review the handover and confirm scope, required engine/model, "
        "and approvals before starting. This controller cannot infer approval."
    ),
    "in_progress": (
        "CHECK: Confirm the recorded required engine/model matches the approved "
        "handover and is available. Continue only with that same engine/model; "
        "no agent launch or fallback occurs here."
    ),
    "paused_usage_reset": (
        "PAUSED: Recheck the required engine/model through an authorised reliable "
        "signal or explicit confirmation. Resume only with the same engine/model; "
        "unknown availability remains blocked."
    ),
    "blocked_engine_unavailable": (
        "REMAIN BLOCKED: Wait or recheck the required engine/model. Do not switch "
        "to an alternate or weaker model."
    ),
    "blocked_decision_needed": (
        "STOP: Obtain the user's explicit decision or approval. Silence is not approval."
    ),
    "ready_for_chatgpt_review": (
        "NEXT: ChatGPT reviews the implementation against approved scope and evidence. "
        "A green workflow alone does not establish project completion."
    ),
    "done": (
        "VERIFY BEFORE CLOSING: Check that every acceptance criterion has evidence. "
        "This controller does not independently certify completion."
    ),
}


def recommend_next_action(state: dict[str, Any]) -> str:
    """Return conservative guidance for a schema-valid checkpoint."""
    return ACTIONS[state["status"]]


def load_and_validate(checkpoint_path: str | Path, repo_root: Path, schema_path: Path = DEFAULT_SCHEMA) -> dict[str, Any]:
    """Read a repository-local checkpoint and fail closed on invalid input."""
    root = repo_root.resolve()
    checkpoint_path = Path(checkpoint_path)
    candidate = checkpoint_path if checkpoint_path.is_absolute() else root / checkpoint_path
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("checkpoint path must remain inside the checked-out repository") from exc

    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        state = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read checkpoint/schema JSON: {exc}") from exc

    errors = validate_state(state, schema)
    if errors:
        raise ValueError("invalid checkpoint:\n- " + "\n- ".join(errors))
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", nargs="?", default="controller/state.example.json")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    args = parser.parse_args(argv)

    try:
        state = load_and_validate(args.checkpoint, args.repo_root, args.schema)
    except ValueError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 1

    recommendation = recommend_next_action(state)
    decision = state["decision"]
    decision_lines = ""
    if decision["question"]:
        decision_lines = (
            f"- **Decision question:** {decision['question']}\n"
            f"- **Options:** {', '.join(decision['options']) if decision['options'] else 'Not specified'}\n"
            f"- **Recommendation:** {decision['recommendation'] or 'None recorded'}\n"
            f"- **Requested at:** {decision['requested_at'] or 'Not recorded'}\n"
            f"- **Decision record:** {decision['decision_record'] or 'No explicit decision recorded'}\n\n"
        )
    report = (
        "# Controller checkpoint review\n\n"
        f"- **Task:** {state['task_id']}\n"
        f"- **Project:** {state['project_name']}\n"
        f"- **Status:** {state['status']}\n"
        f"- **Required engine/model:** {state['required_engine']['provider']} / "
        f"{state['required_engine']['model']}\n"
        f"- **Recorded next step:** {state['next_step']}\n\n"
        f"**Recommended safe action:** {recommendation}\n\n"
        "This is a manual decision aid only. It does not execute an agent, verify "
        "private usage limits, infer approval, or confirm provider availability.\n"
    )
    print(report)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
