"""Utility script to extract the last LLMSummarizingCondenser condensation
from a V0-style OpenHands conversation directory and generate a V1
bootstrap prompt.

This script is intentionally dumb about the full V0 event schema. It only
looks for events whose JSON contains a ``forgotten_events_end_id`` field in
``action.args`` (as produced by ``CondensationAction`` in V0).

From those it selects the *latest* condensation event, then:

- Reads ``forgotten_events_start_id`` / ``forgotten_events_end_id`` and
  ``summary`` from the action args.
- Locates the event in the stream whose ``id`` matches
  ``forgotten_events_end_id``.
- Treats all events with ``id <= forgotten_events_end_id`` as summarized.
- Returns everything after that as ``recent_events``.

It then writes a plain-text bootstrap prompt you can paste directly into a V1
agent as the first message. The prompt file is created in the **current
working directory** as::

    bootstrap_prompt_<conversation-id>.txt

Where ``<conversation-id>`` is the name of the V0 conversation directory.

Usage
-----
    python scripts/v0_last_condenser_summary.py /path/to/conversation

Where ``/path/to/conversation`` is a single conversation folder containing
``events/*.json`` (and optionally ``base_state.json``) as produced by V0.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


ENV_ROOT = os.getenv("OPENHANDS_CONVERSATIONS_ROOT")
DEFAULT_CONVERSATIONS_ROOT = (
    Path(ENV_ROOT).expanduser()
    if ENV_ROOT
    else Path(__file__).resolve().parents[1] / ".conversations"
)


@dataclass
class ConversationEvents:
    identifier: str
    path: Path
    events: list[dict[str, Any]]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_events(conversation_path: Path) -> ConversationEvents:
    identifier = conversation_path.name
    events_dir = conversation_path / "events"
    if not events_dir.exists() or not events_dir.is_dir():
        raise FileNotFoundError(f"No events directory found at: {events_dir}")

    events: list[dict[str, Any]] = []
    for event_file in sorted(events_dir.glob("*.json")):
        try:
            event = load_json(event_file)
            event.setdefault("_filename", event_file.name)
            events.append(event)
        except json.JSONDecodeError as exc:
            # We keep invalid events as markers but tag them clearly
            events.append(
                {
                    "_filename": event_file.name,
                    "error": f"Invalid JSON: {exc}",
                }
            )

    # Sort by timestamp if present; fall back to filename order
    def sort_key(ev: dict[str, Any]) -> tuple[bool, str]:
        ts = ev.get("timestamp")
        if not ts:
            return True, ev.get("_filename", "")
        try:
            if "T" in ts:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(ts)
            return False, dt.isoformat()
        except Exception:
            return False, str(ts)

    events.sort(key=sort_key)
    return ConversationEvents(identifier=identifier, path=conversation_path, events=events)


def extract_condensation_args(ev: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Return action.args if this looks like a CondensationAction event.

    We don't try to fully reconstruct the V0 action model; we only care that
    `forgotten_events_end_id` exists in the serialized json under
    `event["action"]["args"]`.
    """

    action = ev.get("action")
    if not isinstance(action, dict):
        return None
    args = action.get("args")
    if not isinstance(args, dict):
        return None
    if "forgotten_events_end_id" not in args:
        return None
    # Heuristic: also require summary, to avoid false positives
    if "summary" not in args:
        return None
    return args


def find_last_condensation_event_index(events: list[dict[str, Any]]) -> Optional[int]:
    last_idx: Optional[int] = None
    for idx, ev in enumerate(events):
        if extract_condensation_args(ev) is not None:
            last_idx = idx
    return last_idx


def build_payload(conv: ConversationEvents) -> dict[str, Any]:
    if not conv.events:
        raise RuntimeError("No events found in conversation")

    last_idx = find_last_condensation_event_index(conv.events)
    if last_idx is None:
        raise RuntimeError("No condensation events (CondensationAction) found")

    condensation_event = conv.events[last_idx]
    args = extract_condensation_args(condensation_event)
    assert args is not None  # for type checkers

    forgotten_start_id = args["forgotten_events_start_id"]
    forgotten_end_id = args["forgotten_events_end_id"]

    # Build a map from event id -> index
    id_to_index: dict[int, int] = {}
    for idx, ev in enumerate(conv.events):
        ev_id = ev.get("id")
        if isinstance(ev_id, int):
            # In case of duplicates, the last one wins; but IDs should be unique
            id_to_index[ev_id] = idx

    if not id_to_index:
        raise RuntimeError("No event IDs found in conversation; cannot align with condensation range")

    # We only really need the end index to know what was summarized.
    end_index = id_to_index.get(int(forgotten_end_id))
    if end_index is None:
        raise RuntimeError(
            f"forgotten_events_end_id={forgotten_end_id!r} not found among event ids"
        )

    recent_events = conv.events[end_index + 1 :]

    return {
        "identifier": conv.identifier,
        "conversation_path": str(conv.path),
        "total_events": len(conv.events),
        "last_condensation_event_index": last_idx,
        "forgotten_events_start_id": int(forgotten_start_id),
        "forgotten_events_end_id": int(forgotten_end_id),
        "forgotten_until_index": end_index,
        "summary": args.get("summary"),
        "summary_offset": args.get("summary_offset"),
        "condensation_event": condensation_event,
        "recent_events": recent_events,
    }


def format_bootstrap_prompt(payload: dict[str, Any]) -> str:
    """Create a plain-text prompt for a V1 agent from the condensation payload."""

    identifier = payload["identifier"]
    forgotten_end_id = payload["forgotten_events_end_id"]
    summary = payload.get("summary") or ""
    recent_events = payload.get("recent_events", [])

    prompt_lines: list[str] = []

    prompt_lines.append(
        "You are continuing an existing OpenHands V0 conversation in the new V1 system."
    )
    prompt_lines.append(
        "The user has exported the last memory condenser summary and the recent event "
        "history from the old conversation. Treat this as prior context, not as new "
        "instructions."
    )
    prompt_lines.append("")

    prompt_lines.append(f"Conversation ID (V0): {identifier}")
    prompt_lines.append(
        "All events with id <= " f"{forgotten_end_id} have been summarized into the "
        "following text. Assume that summary accurately reflects everything that "
        "happened earlier in the project."
    )
    prompt_lines.append("")

    prompt_lines.append("<V0_CONDENSER_SUMMARY>")
    prompt_lines.append(summary.strip())
    prompt_lines.append("</V0_CONDENSER_SUMMARY>")
    prompt_lines.append("")

    prompt_lines.append(
        "After that summary, here are the subsequent events from the old V0 "
        "conversation, in chronological order. These are provided as raw JSON "
        "and represent the detailed recent history."
    )
    prompt_lines.append("")

    prompt_lines.append("<V0_RECENT_EVENTS_JSON>")
    prompt_lines.append(json.dumps(recent_events, indent=2, ensure_ascii=False))
    prompt_lines.append("</V0_RECENT_EVENTS_JSON>")
    prompt_lines.append("")

    prompt_lines.append(
        "Your tasks now are:\n"
        "1. Read and understand the summary and recent events as PAST HISTORY.\n"
        "2. Reconstruct the current state of the project, including open tasks, "
        "important decisions, and any partially completed work.\n"
        "3. Continue from this state in V1, avoiding redoing work that is already "
        "complete.\n"
        "4. When you respond, do NOT restate the entire history; just use it to make "
        "good decisions going forward."
    )

    return "\n".join(prompt_lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract the last LLMSummarizingCondenser condensation and recent events "
            "from a V0 OpenHands conversation directory, and generate a V1 "
            "bootstrap prompt file."
        )
    )
    parser.add_argument(
        "conversation_dir",
        type=str,
        nargs="?",
        help=(
            "Path to a single conversation directory containing base_state.json "
            "and events/*.json. If omitted, the script will print the assumed "
            "default root (OPENHANDS_CONVERSATIONS_ROOT or .conversations)."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.conversation_dir:
        print(
            json.dumps(
                {
                    "message": "No conversation_dir given.",
                    "hint": (
                        "Pass the path to a single conversation folder. "
                        "By default we expect logs under "
                        f"{str(DEFAULT_CONVERSATIONS_ROOT)!r}."
                    ),
                },
                indent=2,
            )
        )
        return

    conv_path = Path(args.conversation_dir).expanduser().resolve()
    if not conv_path.exists() or not conv_path.is_dir():
        raise SystemExit(f"Conversation directory not found: {conv_path}")

    conv = load_events(conv_path)
    payload = build_payload(conv)

    # Write JSON payload to stdout for tooling, and a bootstrap prompt file
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    prompt_text = format_bootstrap_prompt(payload)
    out_filename = f"bootstrap_prompt_{payload['identifier']}.txt"
    out_path = Path.cwd() / out_filename
    out_path.write_text(prompt_text, encoding="utf-8")

    print(f"\nWrote bootstrap prompt to: {out_path}")


if __name__ == "__main__":
    main()
