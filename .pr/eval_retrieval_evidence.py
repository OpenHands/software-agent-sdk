"""Measure native history retrieval separately from final-answer correctness."""

import json
from collections.abc import Sequence
from typing import Any

from openhands.sdk.event import ActionEvent, Event, ObservationEvent
from openhands.sdk.tool.builtins.conversation_history import (
    ConversationHistoryAction,
    ConversationHistoryObservation,
    history_event_text,
)


def inspect_history_retrieval(
    events: Sequence[Event],
    start_index: int,
    target_marker: str,
    source_ids: Sequence[str],
) -> dict[str, Any]:
    """Count calls and verified results after a reset boundary.

    Hidden-source counts count successful calls, not individual search matches.
    ``full_read_evidence`` means a hidden original source's read contains the
    target marker; it does not claim every character of that source was read.
    """
    if not 0 <= start_index <= len(events):
        raise ValueError("start_index must identify a boundary in events")
    sources = set(source_ids)
    target_ids = (
        [
            event.id
            for event in events
            if event.id in sources
            and (text := history_event_text(event)) is not None
            and target_marker in text
        ]
        if target_marker
        else list(dict.fromkeys(source_ids))
    )
    targets = set(target_ids)
    result: dict[str, Any] = {
        "search_calls": 0,
        "read_calls": 0,
        "hidden_source_searches": 0,
        "hidden_source_reads": 0,
        "search_snippet_contains_target": False,
        "read_contains_target": False,
        "full_read_evidence": False,
        "search_to_read_evidence": False,
        "read_offsets": [],
        "target_event_ids": target_ids,
    }
    actions: dict[str, tuple[ActionEvent, frozenset[str]]] = {}
    searched_sources: set[str] = set()
    for event in events[start_index:]:
        if (
            isinstance(event, ActionEvent)
            and event.tool_name == "conversation_history"
            and isinstance(event.action, ConversationHistoryAction)
        ):
            result[f"{event.action.command}_calls"] += 1
            # A same-batch read was selected before its search result existed.
            actions[event.id] = (event, frozenset(searched_sources))
            continue
        if not (
            isinstance(event, ObservationEvent)
            and event.tool_name == "conversation_history"
            and isinstance(event.observation, ConversationHistoryObservation)
            and not event.observation.is_error
        ):
            continue
        matched = actions.get(event.action_id)
        if matched is None or matched[0].tool_call_id != event.tool_call_id:
            continue
        action_event, prior_searches = matched
        action = action_event.action
        assert isinstance(action, ConversationHistoryAction)
        try:
            payload = json.loads(event.observation.text)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if action.command == "search":
            matches = payload.get("matches", [])
            if not isinstance(matches, list):
                continue
            hidden_sources: set[str] = set()
            for match in matches:
                if not isinstance(match, dict):
                    continue
                source_id = match.get("event_id")
                if source_id in sources and match.get("in_active_view") is False:
                    hidden_sources.add(source_id)
                snippet = match.get("snippet")
                if target_marker and isinstance(snippet, str):
                    result["search_snippet_contains_target"] |= target_marker in snippet
            searched_sources.update(hidden_sources)
            result["hidden_source_searches"] += bool(hidden_sources)
            continue
        text = payload.get("text")
        if (
            not isinstance(text, str)
            or payload.get("event_id") != action.event_id
            or payload.get("offset") != action.offset
        ):
            continue
        hidden = payload.get("in_active_view") is False
        contains_target = bool(target_marker) and target_marker in text
        result["read_contains_target"] |= contains_target
        result["hidden_source_reads"] += hidden and action.event_id in sources
        result["full_read_evidence"] |= (
            hidden and action.event_id in targets and contains_target
        )
        result["search_to_read_evidence"] |= (
            hidden and action.event_id in targets and action.event_id in prior_searches
        )
        result["read_offsets"].append(
            {
                "event_id": action.event_id,
                "offset": action.offset,
                "next_offset": payload.get("next_offset"),
                "chars": len(text),
                "hidden": hidden,
                "contains_target": contains_target,
            }
        )
    return result
