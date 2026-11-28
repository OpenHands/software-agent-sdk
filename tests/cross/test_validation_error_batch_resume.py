"""Resume a saved history whose tool-call batch was split by a validation error.

Older producers persisted a rejected call's ``ActionEvent(action=None)`` and
``AgentErrorEvent`` between the sibling actions of one LLM response. These tests
save such a synthetic legacy stream to disk, resume it through the normal
``LocalConversation`` path, and check the next request against a scripted
transport. Nothing is executed again and the saved events are not rewritten.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from litellm.litellm_core_utils.prompt_templates.factory import (
    _bedrock_converse_messages_pt,
    anthropic_messages_pt,
)
from pydantic import SecretStr, ValidationError

from openhands.sdk import LLM, Agent
from openhands.sdk.context.view import View
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    Event,
    LLMConvertibleEvent,
    MessageEvent,
    ObservationEvent,
    SecurityAnalysisEvent,
)
from openhands.sdk.event.llm_convertible.observation import ToolValidationError
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.file_editor.definition import (
    FileEditorAction,
    FileEditorObservation,
)
from openhands.tools.file_editor.impl import FileEditorExecutor
from openhands.tools.terminal import TerminalTool
from openhands.tools.terminal.definition import TerminalAction, TerminalObservation
from openhands.tools.terminal.impl import TerminalExecutor
from tests.conftest import create_mock_litellm_response


pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="TerminalTool requires the Unix terminal backend.",
)

register_tool("TerminalTool", TerminalTool)
register_tool("FileEditorTool", FileEditorTool)

RESPONSE_ID = "resp-synthetic-7f3a"
CALL_IDS = ["call_A", "call_B", "call_C", "call_D", "call_E"]
FOLLOW_UP = "Synthetic follow-up: summarize the fixture files."


def _tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> MessageToolCall:
    return MessageToolCall(
        id=call_id, name=name, arguments=json.dumps(arguments), origin="completion"
    )


def _file_editor_action(call_id: str, path: Path) -> ActionEvent:
    arguments = {"command": "view", "path": str(path)}
    return ActionEvent(
        thought=[],
        action=FileEditorAction.model_validate(arguments),
        tool_name="file_editor",
        tool_call_id=call_id,
        tool_call=_tool_call(call_id, "file_editor", arguments),
        llm_response_id=RESPONSE_ID,
        security_risk=SecurityRisk.LOW,
    )


def _file_editor_observation(action: ActionEvent, text: str) -> ObservationEvent:
    assert isinstance(action.action, FileEditorAction)
    return ObservationEvent(
        observation=FileEditorObservation.from_text(
            text, command="view", path=action.action.path
        ),
        action_id=action.id,
        tool_name="file_editor",
        tool_call_id=action.tool_call_id,
    )


def _legacy_split_batch(workspace: Path) -> list[Event]:
    """Events in the order the pre-fix producer persisted one five-call response."""
    terminal_args = {"command": "echo synthetic-A"}
    action_a = ActionEvent(
        thought=[TextContent(text="Inspecting the synthetic fixture.")],
        reasoning_content="synthetic reasoning for the whole response",
        action=TerminalAction.model_validate(terminal_args),
        tool_name="terminal",
        tool_call_id="call_A",
        tool_call=_tool_call("call_A", "terminal", terminal_args),
        llm_response_id=RESPONSE_ID,
        security_risk=SecurityRisk.LOW,
    )
    rejected_args = {
        "command": "view",
        "path": str(workspace / "b.txt"),
        "file_path_note": "synthetic extra field",
    }
    with pytest.raises(ValidationError) as rejected:
        FileEditorAction.model_validate(rejected_args)
    action_b = ActionEvent(
        thought=[],
        action=None,
        tool_name="file_editor",
        tool_call_id="call_B",
        tool_call=_tool_call("call_B", "file_editor", rejected_args),
        llm_response_id=RESPONSE_ID,
    )
    error_b = AgentErrorEvent(
        error=(
            f"Error validating tool 'file_editor': {rejected.value}. "
            f"Parameters provided: {list(rejected_args)}"
        ),
        tool_name="file_editor",
        tool_call_id="call_B",
        validation_errors=[
            ToolValidationError(type=detail["type"], loc=detail["loc"])
            for detail in rejected.value.errors(include_url=False)
        ],
    )
    action_c, action_d, action_e = (
        _file_editor_action(call_id, workspace / f"{call_id[-1].lower()}.txt")
        for call_id in CALL_IDS[2:]
    )
    observation_a = ObservationEvent(
        observation=TerminalObservation.from_text(
            "synthetic-A", command="echo synthetic-A", exit_code=0
        ),
        action_id=action_a.id,
        tool_name="terminal",
        tool_call_id="call_A",
    )
    return [
        action_a,
        action_b,
        error_b,
        action_c,
        action_d,
        action_e,
        SecurityAnalysisEvent(
            analyzer="SyntheticAnalyzer",
            policy="NeverConfirm",
            risks={
                a.id: SecurityRisk.LOW for a in (action_a, action_c, action_d, action_e)
            },
        ),
        observation_a,
        _file_editor_observation(action_c, "synthetic contents of C"),
        _file_editor_observation(action_d, "synthetic contents of D"),
        _file_editor_observation(action_e, "synthetic contents of E"),
    ]


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") for block in content)


def _assert_one_contiguous_batch(messages: list[dict[str, Any]]) -> dict[str, str]:
    """Return tool results by call id after checking the batch layout."""
    batches = [i for i, m in enumerate(messages) if m.get("tool_calls")]
    assert len(batches) == 1
    start = batches[0]
    assert [call["id"] for call in messages[start]["tool_calls"]] == CALL_IDS
    results = messages[start + 1 : start + 1 + len(CALL_IDS)]
    assert [m["role"] for m in results] == ["tool"] * len(CALL_IDS)
    assert sorted(m["tool_call_id"] for m in results) == CALL_IDS
    next_turn = messages[start + 1 + len(CALL_IDS)]
    assert next_turn["role"] == "user"
    assert FOLLOW_UP in _text(next_turn["content"])
    return {m["tool_call_id"]: _text(m["content"]) for m in results}


def _assert_provider_formats_keep_batch(messages: list[dict[str, Any]]) -> None:
    """Offline LiteLLM Anthropic and Bedrock Converse serialization of the request."""
    history = [m for m in messages if m["role"] != "system"]

    anthropic: list[dict[str, Any]] = json.loads(
        json.dumps(
            anthropic_messages_pt(
                json.loads(json.dumps(history)),
                model="claude-sonnet-4-5",
                llm_provider="anthropic",
            )
        )
    )
    uses = [
        i
        for i, m in enumerate(anthropic)
        if any(b.get("type") == "tool_use" for b in m["content"])
    ]
    assert len(uses) == 1
    assert [
        b["id"] for b in anthropic[uses[0]]["content"] if b.get("type") == "tool_use"
    ] == CALL_IDS
    tool_results = {
        b["tool_use_id"]: json.dumps(b["content"])
        for b in anthropic[uses[0] + 1]["content"]
        if b.get("type") == "tool_result"
    }
    assert sorted(tool_results) == CALL_IDS
    assert "synthetic-A" in tool_results["call_A"]
    assert "skipped" not in json.dumps(anthropic)

    bedrock: list[dict[str, Any]] = json.loads(
        json.dumps(
            _bedrock_converse_messages_pt(
                json.loads(json.dumps(history)),
                model="anthropic.claude-sonnet-4-5-20250929-v1:0",
                llm_provider="bedrock_converse",
            )
        )
    )
    uses = [
        i for i, m in enumerate(bedrock) if any("toolUse" in b for b in m["content"])
    ]
    assert len(uses) == 1
    assert [
        b["toolUse"]["toolUseId"] for b in bedrock[uses[0]]["content"] if "toolUse" in b
    ] == CALL_IDS
    assert (
        sorted(
            b["toolResult"]["toolUseId"]
            for b in bedrock[uses[0] + 1]["content"]
            if "toolResult" in b
        )
        == CALL_IDS
    )


def _conversation(
    agent: Agent, workspace: Path, persistence: Path, conversation_id: uuid.UUID
) -> LocalConversation:
    return LocalConversation(
        agent=agent,
        workspace=workspace,
        persistence_dir=persistence,
        conversation_id=conversation_id,
        visualizer=None,
    )


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_resumed_legacy_split_batch_is_sent_as_one_contiguous_batch(
    mock_completion, tmp_path: Path
):
    requests: list[list[dict[str, Any]]] = []

    def scripted_transport(*_args: Any, **kwargs: Any):
        requests.append(json.loads(json.dumps(kwargs["messages"])))
        return create_mock_litellm_response(
            content="Synthetic follow-up acknowledged.", finish_reason="stop"
        )

    mock_completion.side_effect = scripted_transport
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    persistence = tmp_path / "persist"
    conversation_id = uuid.uuid4()
    agent = Agent(
        llm=LLM(
            model="gpt-4o",
            api_key=SecretStr("synthetic-placeholder"),
            usage_id="test-llm",
        ),
        tools=[Tool(name="TerminalTool"), Tool(name="FileEditorTool")],
    )
    legacy_events = _legacy_split_batch(workspace)

    saved = _conversation(agent, workspace, persistence, conversation_id)
    try:
        saved.send_message("Synthetic task: inspect the fixture files.")
        for event in legacy_events:
            saved._on_event(event)
        saved.send_message(FOLLOW_UP)
    finally:
        saved.close()
    events_dir = persistence / conversation_id.hex / "events"
    saved_files = {p.name: p.read_bytes() for p in events_dir.glob("event-*.json")}

    with (
        patch.object(TerminalExecutor, "__call__") as terminal_spy,
        patch.object(FileEditorExecutor, "__call__") as file_editor_spy,
    ):
        resumed = _conversation(agent, workspace, persistence, conversation_id)
        try:
            restored = list(resumed.state.events)
            history = LLMConvertibleEvent.events_to_messages(
                View.from_events(restored).events
            )
            resumed.run()
            status = resumed.state.execution_status
            new_events = list(resumed.state.events)[len(restored) :]
        finally:
            resumed.close()

    restored_ids = [e.id for e in restored]
    legacy_ids = [e.id for e in legacy_events]
    start = restored_ids.index(legacy_ids[0])
    assert restored_ids[start : start + len(legacy_ids)] == legacy_ids
    assert isinstance(restored[start + len(legacy_ids)], MessageEvent)

    tool_call_groups = [[c.id for c in m.tool_calls] for m in history if m.tool_calls]
    assert tool_call_groups == [CALL_IDS]
    batch_at = next(i for i, m in enumerate(history) if m.tool_calls)
    assert [m.role for m in history[batch_at + 1 :]] == ["tool"] * 5 + ["user"]
    assert history[batch_at].reasoning_content == (
        "synthetic reasoning for the whole response"
    )

    assert len(requests) == 1
    results = _assert_one_contiguous_batch(requests[0])
    assert "synthetic-A" in results["call_A"]
    error_b = next(e for e in restored if isinstance(e, AgentErrorEvent))
    assert results["call_B"] == error_b.error
    assert "file_path_note" in results["call_B"]
    _assert_provider_formats_keep_batch(requests[0])

    assert terminal_spy.call_count == file_editor_spy.call_count == 0
    assert status == ConversationExecutionStatus.FINISHED
    assert not [e for e in new_events if isinstance(e, (ActionEvent, ObservationEvent))]
    assert any(
        isinstance(e, MessageEvent)
        and e.source == "agent"
        and "Synthetic follow-up acknowledged."
        in _text([c.model_dump() for c in e.llm_message.content])
        for e in new_events
    )
    assert {
        name: (events_dir / name).read_bytes() for name in saved_files
    } == saved_files
