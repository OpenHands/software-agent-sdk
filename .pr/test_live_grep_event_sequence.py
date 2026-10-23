"""Offline event validation checks; never import or execute the live harness."""

import ast
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest


ARTIFACTS = Path(__file__).parent


def _load_validator() -> Callable[[list[dict[str, Any]]], list[str]]:
    harness = ARTIFACTS / "repro_live_grep_agent.py"
    tree = ast.parse(harness.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "validate_event_sequence"
    )
    namespace: dict[str, Any] = {"Any": Any}
    module = ast.Module(body=[function], type_ignores=[])
    exec(compile(module, str(harness), "exec"), namespace)
    return cast(
        Callable[[list[dict[str, Any]]], list[str]],
        namespace["validate_event_sequence"],
    )


validate_event_sequence = _load_validator()


def _entries(revision: str = "head") -> list[dict[str, Any]]:
    path = ARTIFACTS / "live-grep-2026-10-05" / revision / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize("revision", ["base", "head"])
def test_archived_event_sequence_passes(revision: str) -> None:
    assert validate_event_sequence(_entries(revision)) == []


def test_finish_must_follow_both_grep_observations() -> None:
    entries = _entries()
    finish = entries.pop(5)
    entries.insert(4, finish)
    assert validate_event_sequence(entries)


def test_finish_must_use_a_later_model_response() -> None:
    entries = _entries()
    entries[5]["llm_response_id"] = entries[1]["llm_response_id"]
    assert validate_event_sequence(entries)


@pytest.mark.parametrize("event", ["AgentErrorEvent", "ConversationErrorEvent"])
def test_error_events_fail_validation(event: str) -> None:
    entries = _entries()
    entries.insert(5, {"event": event, "code": "offline-test-error"})
    assert validate_event_sequence(entries)


def test_missing_finish_fails_validation() -> None:
    entries = _entries()[:5]
    assert validate_event_sequence(entries)


def test_extra_finish_fails_validation() -> None:
    entries = _entries()
    extra_finish, extra_observation = _entries()[5:]
    extra_finish["tool_call"]["id"] = "extra-finish"
    extra_finish["llm_response_id"] = "extra-response"
    extra_observation["tool_call_id"] = "extra-finish"
    entries.extend([extra_finish, extra_observation])
    assert validate_event_sequence(entries)


@pytest.mark.parametrize("tool_name", ["grep", "terminal"])
def test_extra_call_fails_validation(tool_name: str) -> None:
    entries = _entries()
    extra_call = _entries()[1]
    extra_call["tool_call"].update(id="extra-call", name=tool_name)
    extra_observation = _entries()[3]
    extra_observation.update(tool_call_id="extra-call", tool_name=tool_name)
    entries[5:5] = [extra_call, extra_observation]
    assert validate_event_sequence(entries)


@pytest.mark.parametrize("observation_index", [3, 6])
def test_observation_must_match_its_call_id(observation_index: int) -> None:
    entries = _entries()
    entries[observation_index]["tool_call_id"] = "unmatched-call"
    assert validate_event_sequence(entries)
