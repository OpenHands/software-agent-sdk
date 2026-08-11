from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import PrivateAttr, SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.event import ActionEvent
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.llm.exceptions import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)
from openhands.sdk.task_outcome import FinishTaskOutcomeResponse
from openhands.sdk.tool.builtins import BUILT_IN_TOOL_CLASSES, BUILT_IN_TOOLS
from openhands.sdk.tool.builtins.finish import FinishAction, FinishTool


class RaisingLLM(LLM):
    _exc_factory: Callable[[], Exception] = PrivateAttr()

    def __init__(self, exc_factory: Callable[[], Exception]):
        super().__init__(model="test-model", usage_id="test-llm")
        self._exc_factory = exc_factory

    def completion(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise self._exc_factory()

    async def acompletion(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise self._exc_factory()


def _conversation(tmp_path: Path, *, llm: LLM | None = None) -> LocalConversation:
    llm = llm or LLM(
        model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test"
    )
    return LocalConversation(
        agent=Agent(llm=llm, tools=[]),
        workspace=str(tmp_path),
        persistence_dir=str(tmp_path / "conversation"),
        visualizer=None,
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


def _assert_task_outcome(
    conv: LocalConversation,
    *,
    status: str,
    blocker_type: str,
    recoverable: bool,
    needs_user_action: bool,
    terminal_reason: str,
    summary: str,
) -> None:
    outcome = conv.state.task_outcome
    assert outcome is not None
    assert outcome.status == status
    assert outcome.source == "system"
    assert outcome.summary == summary
    assert outcome.needs_user_action is needs_user_action
    assert outcome.terminal_reason == terminal_reason
    assert outcome.reported_at is not None
    assert len(outcome.blockers) == 1
    blocker = outcome.blockers[0]
    assert blocker.type == blocker_type
    assert blocker.message == summary
    assert blocker.recoverable is recoverable


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
    conv = _conversation(tmp_path)
    conv._ensure_agent_ready()
    finish_tool = conv.agent.tools_map[FinishTool.name]
    assert isinstance(finish_tool, FinishTool)
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
    assert conv.state.task_outcome.source == "agent"
    assert conv.state.task_outcome.reported_at is not None
    assert conv.state.task_outcome.terminal_reason == "finish_action"
    assert conv.state.task_outcome.needs_user_action is True
    assert conv.state.task_outcome.blockers[0].type == "missing_secret"

    conv.close()


@pytest.mark.parametrize(
    (
        "code",
        "detail",
        "expected_status",
        "expected_blocker_type",
        "expected_recoverable",
        "expected_needs_user_action",
    ),
    [
        pytest.param(
            "LLMAuthenticationError",
            "Invalid API key.",
            "blocked",
            "auth",
            True,
            True,
            id="typed-llm-authentication-error",
        ),
        pytest.param(
            "OpenAIError",
            "Error code: 401 - invalid api key",
            "blocked",
            "auth",
            True,
            True,
            id="provider-wrapper-authentication-error",
        ),
        pytest.param(
            "LLMRateLimitError",
            "Rate limit exceeded.",
            "blocked",
            "rate_limit",
            True,
            False,
            id="typed-rate-limit-error",
        ),
        pytest.param(
            "OpenAIError",
            "Connection error: service temporarily unavailable",
            "blocked",
            "transient",
            True,
            False,
            id="provider-wrapper-transient-error",
        ),
        pytest.param(
            "LLMTimeoutError",
            "LLM request timed out.",
            "blocked",
            "transient",
            True,
            False,
            id="typed-timeout-error",
        ),
        pytest.param(
            "LLMBadRequestError",
            "model not found",
            "blocked",
            "config",
            True,
            True,
            id="typed-configuration-error",
        ),
        pytest.param(
            "MaxBudgetReached",
            "Agent reached maximum budget limit.",
            "blocked",
            "quota",
            True,
            True,
            id="quota-error",
        ),
        pytest.param(
            "KeyError",
            "missing event field",
            "failed",
            "internal",
            False,
            False,
            id="internal-error",
        ),
        pytest.param(
            "RuntimeExploded",
            "Unexpected harness failure.",
            "failed",
            "unknown",
            False,
            False,
            id="unknown-error",
        ),
    ],
)
def test_conversation_error_event_records_classified_system_task_outcome(
    tmp_path,
    code: str,
    detail: str,
    expected_status: str,
    expected_blocker_type: str,
    expected_recoverable: bool,
    expected_needs_user_action: bool,
):
    conv = _conversation(tmp_path)

    with conv.state:
        conv._on_event(
            ConversationErrorEvent(
                source="environment",
                code=code,
                detail=detail,
            )
        )

    _assert_task_outcome(
        conv,
        status=expected_status,
        blocker_type=expected_blocker_type,
        recoverable=expected_recoverable,
        needs_user_action=expected_needs_user_action,
        terminal_reason=code,
        summary=detail,
    )

    conv.close()


@pytest.mark.parametrize(
    (
        "exc_factory",
        "expected_code",
        "expected_detail",
        "expected_blocker_type",
        "expected_needs_user_action",
    ),
    [
        pytest.param(
            lambda: LLMAuthenticationError("Invalid API key."),
            "LLMAuthenticationError",
            "Invalid API key.",
            "auth",
            True,
            id="authentication-failure-from-llm",
        ),
        pytest.param(
            lambda: LLMServiceUnavailableError(
                "Connection error: service temporarily unavailable"
            ),
            "LLMServiceUnavailableError",
            "Connection error: service temporarily unavailable",
            "transient",
            False,
            id="service-unavailable-from-llm",
        ),
        pytest.param(
            lambda: LLMRateLimitError("Rate limit exceeded."),
            "LLMRateLimitError",
            "Rate limit exceeded.",
            "rate_limit",
            False,
            id="rate-limit-from-llm",
        ),
        pytest.param(
            lambda: LLMTimeoutError("LLM request timed out."),
            "LLMTimeoutError",
            "LLM request timed out.",
            "transient",
            False,
            id="timeout-from-llm",
        ),
        pytest.param(
            lambda: LLMBadRequestError("model not found"),
            "LLMBadRequestError",
            "model not found",
            "config",
            True,
            id="configuration-failure-from-llm",
        ),
    ],
)
def test_run_loop_records_task_outcome_for_llm_failures(
    tmp_path,
    exc_factory: Callable[[], Exception],
    expected_code: str,
    expected_detail: str,
    expected_blocker_type: str,
    expected_needs_user_action: bool,
):
    conv = _conversation(tmp_path, llm=RaisingLLM(exc_factory))
    conv.send_message("Do the task.")

    with pytest.raises(ConversationRunError):
        conv.run()

    errors = [
        event
        for event in conv.state.events
        if isinstance(event, ConversationErrorEvent)
    ]
    assert len(errors) == 1
    assert errors[0].code == expected_code
    assert errors[0].detail == expected_detail

    _assert_task_outcome(
        conv,
        status="blocked",
        blocker_type=expected_blocker_type,
        recoverable=True,
        needs_user_action=expected_needs_user_action,
        terminal_reason=expected_code,
        summary=expected_detail,
    )

    conv.close()
