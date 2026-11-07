"""Conversations persisted by older releases resume with the history they had.

Each case is a conversation that ``tests/fixtures/conversations/generate.py``
wrote with a published release, plus the LLM input that release produced when
it resumed the conversation. The current SDK must resume it with the same LLM
input (after ``normalize``), send the LLM well-formed tool history, and leave
the old event files untouched.

Checked-in cases live under ``tests/fixtures/conversations/v*/``. CI adds the
latest release's through ``OH_PERSISTED_CONVERSATIONS_DIR``; see
``.github/scripts/check_persisted_conversation_compat.py``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest


FIXTURES = Path(__file__).parents[1] / "fixtures" / "conversations"

_spec = importlib.util.spec_from_file_location("generate", FIXTURES / "generate.py")
assert _spec is not None and _spec.loader is not None
generator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generator)

# Scenarios the current SDK resumes wrongly. Strict, so fixing one fails here
# until its entry is removed.
KNOWN_BROKEN = {
    "awaiting_confirmation": (
        "Restoring rebuilds the cached view with View.from_events, whose "
        "tool-call matching drops the still-pending actions; approved tool "
        "results then reach the LLM without their calls."
    ),
}


def _cases() -> list[Any]:
    roots = [FIXTURES]
    if extra := os.environ.get("OH_PERSISTED_CONVERSATIONS_DIR"):
        roots.append(Path(extra))
    cases = sorted(m.parent for root in roots for m in root.glob("*/*/manifest.json"))
    return [
        pytest.param(
            case,
            id=f"{case.parent.name}/{case.name}",
            marks=[pytest.mark.xfail(reason=KNOWN_BROKEN[case.name], strict=True)]
            if case.name in KNOWN_BROKEN
            else [],
        )
        for case in cases
    ]


def _event_files(root: Path) -> dict[str, bytes]:
    events = root.glob("*/events/event-*.json")  # not the log's own markers
    return {str(p.relative_to(root)): p.read_bytes() for p in events}


def _assert_tool_calls_answered(view: list[dict[str, Any]]) -> None:
    """Every tool result answers an earlier call and every call gets one,
    which is what LLM providers require."""
    unanswered: list[str] = []
    for message in view:
        if message["role"] == "tool":
            call_id = message["tool_call_id"]
            assert call_id in unanswered, f"tool result {call_id} answers no call"
            unanswered.remove(call_id)
        unanswered += [call_id for call_id, *_ in message.get("tool_calls", [])]
    assert not unanswered, f"tool calls without a result: {unanswered}"


@pytest.mark.parametrize("case", _cases())
def test_resumed_conversation_keeps_its_history(
    case: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    manifest = json.loads((case / "manifest.json").read_text())
    root = shutil.copytree(case, tmp_path / case.name)
    written = _event_files(root)

    resumed = generator.normalize(generator.probe(manifest["scenario"], root))

    assert resumed == generator.normalize(manifest["resumed_llm_input"]), (
        f"openhands-sdk {manifest['sdk_version']} resumed this conversation "
        "with different LLM input. If the change is intended, make normalize() "
        "in tests/fixtures/conversations/generate.py ignore it."
    )
    _assert_tool_calls_answered(resumed)
    after = _event_files(root)
    assert {name: after.get(name) for name in written} == written, (
        "resuming rewrote or deleted event files written by an older release"
    )


def test_every_scenario_has_checked_in_cases() -> None:
    checked_in = {m.parent.name for m in FIXTURES.glob("*/*/manifest.json")}
    assert checked_in == set(generator.SCENARIOS)
