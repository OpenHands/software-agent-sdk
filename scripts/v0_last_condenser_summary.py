"""Utility script to extract the last LLMSummarizingCondenser condensation
from a V0-style OpenHands conversation directory and generate a V1
bootstrap prompt.

This script doesn't know the full V0 event schema. It only
looks for events whose JSON contains a ``forgotten_events_end_id`` field in
``action.args`` (as produced by ``CondensationAction`` in V0).

The serialized event is like:
```
{
  "id": 5202,
  "timestamp": "...",
  "source": "agent",
  "message": "Summary: ...",
  "action": "condensation",
  "llm_metrics": {...},
  "args": {
    "forgotten_events_start_id": 4479,
    "forgotten_events_end_id": 4860,
    "summary": "USER_CONTEXT: ...",
    "summary_offset": 8,
    ...
  }
}
```

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
from pathlib import Path
from typing import Any


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

    # We don't need to slurp thousands of events into memory if we're only
    # going to use the tail of the stream. Since V0 names files by integer
    # event id (e.g. 1.json, 2.json, ... 5202.json), we can:
    #
    # 1. Find the last condensation event by streaming files in ascending id
    #    order.
    # 2. Once we know the boundary (forgotten_events_end_id), only read the
    #    events after that id into `events`.
    #
    # To keep the rest of the script simple, we *still* materialize a
    # ConversationEvents object, but with only the suffix of the history we
    # actually care about.

    # First pass: scan for the last condensation event's args.
    last_condensation_args: dict[str, Any] | None = None

    for event_file in sorted(events_dir.glob("*.json"), key=lambda p: int(p.stem)):
        try:
            event = load_json(event_file)
        except json.JSONDecodeError:
            continue

        args = extract_condensation_args(event)
        if args is not None:
            last_condensation_args = args

    # Second pass: depending on whether we found a condenser, decide what
    # portion of the history to load.
    if last_condensation_args is None:
        # No condenser: load *all* events, but still stream in id order and
        # keep invalid JSON as markers.
        for event_file in sorted(events_dir.glob("*.json"), key=lambda p: int(p.stem)):
            try:
                event = load_json(event_file)
                event.setdefault("_filename", event_file.name)
                events.append(event)
            except json.JSONDecodeError as exc:
                events.append(
                    {
                        "_filename": event_file.name,
                        "error": f"Invalid JSON: {exc}",
                    }
                )
    else:
        # We know the condenser's `forgotten_events_end_id`; anything after
        # that id is "recent" for our purposes. The filenames are exactly
        # those ids, so we can load only those files.
        try:
            forgotten_end_id = int(last_condensation_args["forgotten_events_end_id"])
        except Exception:
            forgotten_end_id = None

        for event_file in sorted(events_dir.glob("*.json"), key=lambda p: int(p.stem)):
            try:
                event_id = int(event_file.stem)
            except ValueError:
                continue

            if forgotten_end_id is not None and event_id <= forgotten_end_id:
                # This event is covered by the summary; we don't need to load it.
                continue

            try:
                event = load_json(event_file)
                event.setdefault("_filename", event_file.name)
                events.append(event)
            except json.JSONDecodeError as exc:
                events.append(
                    {
                        "_filename": event_file.name,
                        "error": f"Invalid JSON: {exc}",
                    }
                )

    return ConversationEvents(
        identifier=identifier, path=conversation_path, events=events
    )


def extract_condensation_args(ev: dict[str, Any]) -> dict[str, Any] | None:
    """Return args if this looks like a V0 condensation event.

    In V0 on-disk format, condensation events are serialized with

    - `action` as the string "condensation"
    - the condenser arguments directly under top-level `args`

    We only care that `forgotten_events_end_id` and `summary` exist in
    `event["args"]`.
    """

    if ev.get("action") != "condensation":
        return None

    args = ev.get("args")
    if not isinstance(args, dict):
        return None
    if "forgotten_events_end_id" not in args:
        return None
    # Heuristic: also require summary, to avoid false positives
    if "summary" not in args:
        return None
    return args


def find_last_condensation_event_index(events: list[dict[str, Any]]) -> int | None:
    last_idx: int | None = None
    for idx, ev in enumerate(events):
        if extract_condensation_args(ev) is not None:
            last_idx = idx
    return last_idx


def build_payload(conv: ConversationEvents) -> dict[str, Any]:
    if not conv.events:
        raise RuntimeError("No events found in conversation")

    last_idx = find_last_condensation_event_index(conv.events)

    # If there is no condensation event, fall back to treating the entire
    # event history as "recent". This still gives the V1 agent something
    # usable, just without a prior summary.
    if last_idx is None:
        return {
            "identifier": conv.identifier,
            "conversation_path": str(conv.path),
            "total_events": len(conv.events),
            "last_condensation_event_index": None,
            "forgotten_events_start_id": None,
            "forgotten_events_end_id": None,
            "forgotten_until_index": None,
            "summary": None,
            "summary_offset": None,
            "condensation_event": None,
            "recent_events": conv.events,
        }

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
        raise RuntimeError(
            "No event IDs found in conversation; cannot align with condensation range"
        )

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
    forgotten_end_id = payload.get("forgotten_events_end_id")
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

    if forgotten_end_id is not None and summary.strip():
        prompt_lines.append(
            "All events with id <= "
            f"{forgotten_end_id} have been summarized into the "
            "following text. Assume that summary accurately reflects everything "
            "that happened earlier in the project."
        )
        prompt_lines.append("")

        prompt_lines.append("<V0_CONDENSER_SUMMARY>")
        prompt_lines.append(summary.strip())
        prompt_lines.append("</V0_CONDENSER_SUMMARY>")
        prompt_lines.append("")
    else:
        prompt_lines.append(
            "No prior condensation summary was found. The following events "
            "represent the full available history of the V0 conversation."
        )
        prompt_lines.append("")

    prompt_lines.append(
        "After that summary (or, if none, from the beginning), here are the "
        "events from the old V0 conversation in chronological order. These are "
        "provided as raw JSON and represent the detailed history available to "
        "you."
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
