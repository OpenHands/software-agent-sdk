from __future__ import annotations

import json
import uuid

from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event import ActionEvent
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.task_outcome import FinishTaskOutcomeResponse
from openhands.sdk.tool.builtins import BUILT_IN_TOOL_CLASSES, BUILT_IN_TOOLS
from openhands.sdk.tool.builtins.finish import FinishAction, FinishTool
from openhands.sdk.workspace.local import LocalWorkspace


def _state(tmp_path) -> ConversationState:
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test")
    return ConversationState.create(
        id=uuid.uuid4(),
        agent=Agent(llm=llm, tools=[]),
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
        persistence_dir=str(tmp_path / "conversation"),
    )


def _finish_payload(**outcome_updates):
    outcome = {
        "status": "success",
        "summary": "Task completed.",
        "blockers": [],
        "confidence": 0.9,
        "needs_user_action": False,
    }
    outcome.update(outcome_updates)
    return {"message": "Done.", "task_outcome": outcome}


def _finish_event(tool: FinishTool, payload: dict) -> ActionEvent:
    action = tool.action_from_arguments(payload)
    assert isinstance(action, FinishAction)
    return ActionEvent(
        source="agent",
        tool_name=FinishTool.name,
        tool_call_id="finish-call",
        tool_call=MessageToolCall(
            id="finish-call",
            name=FinishTool.name,
            arguments=json.dumps(payload),
            origin="completion",
        ),
        llm_response_id="response-id",
        action=action,
        thought=[],
        reasoning_content="",
    )


def test_finish_tool_is_default_task_outcome_reporting_tool():
    assert FinishTool in BUILT_IN_TOOLS
    assert "ReportTaskOutcomeTool" not in BUILT_IN_TOOL_CLASSES
    assert BUILT_IN_TOOL_CLASSES["FinishTool"] is FinishTool
    assert FinishTool.name == "finish"

    (tool,) = FinishTool.create()
    assert tool.response_schema is FinishTaskOutcomeResponse
    props = tool._get_tool_schema()["properties"]
    assert "message" in props
    assert "task_outcome" in props


def test_finish_tool_parses_task_outcome_response():
    (tool,) = FinishTool.create()
    payload = _finish_payload(summary="Implemented the change.")

    action = tool.action_from_arguments(payload)
    parsed = tool.parse_response(action)

    assert isinstance(parsed, FinishTaskOutcomeResponse)
    assert parsed.task_outcome.status == "success"
    assert parsed.task_outcome.summary == "Implemented the change."
    assert parsed.task_outcome.blockers == []


def test_finish_tool_parse_last_response_roundtrips_from_event():
    (tool,) = FinishTool.create()
    event = _finish_event(tool, _finish_payload(status="partial_success"))

    result = tool.parse_last_response([event])

    assert isinstance(result, FinishTaskOutcomeResponse)
    assert result.task_outcome.status == "partial_success"


def test_finish_action_event_records_latest_task_outcome(tmp_path):
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test")
    conv = LocalConversation(
        agent=Agent(llm=llm, tools=[]),
        workspace=str(tmp_path),
        persistence_dir=str(tmp_path / "conversation"),
        visualizer=None,
    )
    conv._ensure_agent_ready()
    finish_tool = conv.agent.tools_map[FinishTool.name]
    event = _finish_event(
        finish_tool,
        _finish_payload(
            status="blocked",
            summary="Waiting for credentials.",
            blockers=[
                {
                    "type": "missing_secret",
                    "message": "Missing deployment token.",
                    "recoverable": True,
                }
            ],
            needs_user_action=True,
        ),
    )

    with conv.state:
        conv._on_event(event)

    assert conv.state.task_outcome is not None
    assert conv.state.task_outcome.status == "blocked"
    assert conv.state.task_outcome.summary == "Waiting for credentials."
    assert conv.state.task_outcome.source == "agent_report"
    assert conv.state.task_outcome.reported_at is not None
    assert conv.state.task_outcome.terminal_reason == "finish_action"
    assert conv.state.task_outcome.needs_user_action is True
    assert conv.state.task_outcome.blockers[0].type == "missing_secret"

    conv.close()


def test_conversation_error_event_records_system_task_outcome(tmp_path):
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test")
    conv = LocalConversation(
        agent=Agent(llm=llm, tools=[]),
        workspace=str(tmp_path),
        persistence_dir=str(tmp_path / "conversation"),
        visualizer=None,
    )

    with conv.state:
        conv._on_event(
            ConversationErrorEvent(
                source="environment",
                code="LLMAuthenticationError",
                detail="Invalid API key.",
            )
        )

    assert conv.state.task_outcome is not None
    assert conv.state.task_outcome.status == "blocked"
    assert conv.state.task_outcome.summary == "Invalid API key."
    assert conv.state.task_outcome.source == "system"
    assert conv.state.task_outcome.needs_user_action is True
    assert conv.state.task_outcome.terminal_reason == "LLMAuthenticationError"
    assert conv.state.task_outcome.blockers[0].type == "auth"
    assert conv.state.task_outcome.blockers[0].recoverable is True

    conv.close()


def test_unknown_conversation_error_records_failed_task_outcome(tmp_path):
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test")
    conv = LocalConversation(
        agent=Agent(llm=llm, tools=[]),
        workspace=str(tmp_path),
        persistence_dir=str(tmp_path / "conversation"),
        visualizer=None,
    )

    with conv.state:
        conv._on_event(
            ConversationErrorEvent(
                source="environment",
                code="RuntimeExploded",
                detail="Unexpected harness failure.",
            )
        )

    assert conv.state.task_outcome is not None
    assert conv.state.task_outcome.status == "failed"
    assert conv.state.task_outcome.source == "system"
    assert conv.state.task_outcome.needs_user_action is False
    assert conv.state.task_outcome.blockers[0].type == "unknown"

    conv.close()
