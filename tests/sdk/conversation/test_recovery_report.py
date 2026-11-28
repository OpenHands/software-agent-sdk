import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import Field

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.conversation.recovery import (
    EvidenceCoverage,
    RunOutcome,
    RunReceipt,
    RunReceiptPhase,
    begin_run,
    build_recovery_report,
    finish_run,
)
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.conversation.types import (
    ConversationCallbackType,
    ConversationTokenCallbackType,
)
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.event.llm_convertible import AgentErrorEvent, MessageEvent
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.event.llm_convertible.observation import (
    ObservationEvent,
    UserRejectObservation,
)
from openhands.sdk.event.types import ROOT_PARENT_ID
from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent
from openhands.sdk.tool import Action, Observation


def _receipt(start_event_id: str | None = None) -> RunReceipt:
    return RunReceipt(
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        phase=RunReceiptPhase.SETTLED,
        outcome=RunOutcome.FAILED,
        reason_code="LLMRateLimitError",
        start_event_id=start_event_id,
        end_event_id="error-1",
    )


class _RecoveryAction(Action):
    command: str


class _RecoveryObservation(Observation):
    result: str
    exit_code: int | None = None
    task_list: list[dict[str, str]] = Field(default_factory=list)


def _action_event(event_id: str, call_id: str, command: str) -> ActionEvent:
    return ActionEvent(
        id=event_id,
        source="agent",
        thought=[],
        action=_RecoveryAction(command=command),
        tool_name="terminal",
        tool_call_id=call_id,
        tool_call=MessageToolCall(
            id=call_id,
            name="terminal",
            arguments=json.dumps({"command": command}),
            origin="completion",
        ),
        llm_response_id="response-1",
    )


def test_recovery_report_uses_only_public_assistant_text_and_event_references():
    events = [
        MessageEvent(
            id="user-1",
            parent_id=ROOT_PARENT_ID,
            source="user",
            llm_message=Message(role="user", content=[TextContent(text="do work")]),
        ),
        MessageEvent(
            id="assistant-1",
            parent_id="user-1",
            source="agent",
            llm_message=Message(
                role="assistant",
                content=[TextContent(text="Parser updated; checking the tests.")],
                reasoning_content="private reasoning must not be copied",
            ),
        ),
        ConversationErrorEvent(
            id="error-1",
            parent_id="assistant-1",
            source="environment",
            code="LLMRateLimitError",
            detail="provider response detail is not copied",
        ),
    ]

    report = build_recovery_report(
        _receipt("user-1"), events, ConversationExecutionStatus.ERROR
    )

    assert report.run_id == "run-1"
    assert report.reason_code == "LLMRateLimitError"
    assert report.last_agent_update is not None
    assert report.last_agent_update.event_id == "assistant-1"
    assert report.last_agent_update.text == "Parser updated; checking the tests."
    assert "private reasoning" not in str(report.model_dump())
    assert "provider response detail" not in str(report.model_dump())
    assert report.evidence_coverage == "complete"


def test_recovery_report_flags_restart_action_as_outcome_unknown():
    action = _action_event("action-1", "call-1", "deploy")
    interrupted = AgentErrorEvent(
        id="interrupt-1",
        parent_id="action-1",
        source="agent",
        tool_name="terminal",
        tool_call_id="call-1",
        error="Tool call interrupted before completion.",
        recovery_reason="server_restart",
    )
    error = ConversationErrorEvent(
        id="error-1",
        parent_id="interrupt-1",
        source="environment",
        code="ServerRestarted",
        detail="The server restarted.",
    )

    report = build_recovery_report(
        _receipt("user-1"),
        [action, interrupted, error],
        ConversationExecutionStatus.ERROR,
    )

    assert len(report.uncertain_operations) == 1
    assert report.uncertain_operations[0].action_event_id == "action-1"
    assert report.uncertain_operations[0].tool_name == "terminal"
    assert report.uncertain_operations[0].possible_side_effects is True
    assert "deploy" not in str(report.model_dump())


def test_recovery_report_matches_tool_results_by_action_event_id():
    completed = _action_event("action-completed", "reused-call-id", "echo done")
    interrupted = _action_event("action-interrupted", "reused-call-id", "deploy")
    result = ObservationEvent(
        id="result-1",
        parent_id=completed.id,
        source="environment",
        tool_name="terminal",
        tool_call_id=completed.tool_call_id,
        action_id=completed.id,
        observation=_RecoveryObservation(result="done"),
    )

    report = build_recovery_report(
        _receipt("user-1"),
        [completed, result, interrupted],
        ConversationExecutionStatus.ERROR,
    )

    assert [item.action_event_id for item in report.uncertain_operations] == [
        "action-interrupted"
    ]
    assert "deploy" not in report.model_dump_json()


def test_recovery_report_does_not_flag_rejected_action_as_uncertain():
    action = _action_event("action-rejected", "call-rejected", "rm -rf /")
    rejection = UserRejectObservation(
        id="rejection-1",
        parent_id=action.id,
        source="user",
        tool_name=action.tool_name,
        tool_call_id=action.tool_call_id,
        action_id=action.id,
    )

    report = build_recovery_report(
        _receipt("user-1"),
        [action, rejection],
        ConversationExecutionStatus.ERROR,
    )

    assert report.uncertain_operations == []
    assert "rm -rf /" not in report.model_dump_json()


def test_report_retains_incomplete_tasks_and_soft_timeout_uncertainty():
    action = _action_event("action-1", "call-1", "deploy")
    result = ObservationEvent(
        id="result-1",
        parent_id=action.id,
        source="environment",
        tool_name="terminal",
        tool_call_id=action.tool_call_id,
        action_id=action.id,
        observation=_RecoveryObservation(
            result="still running",
            exit_code=-1,
            task_list=[
                {"title": "Verify deployment", "status": "in_progress"},
                {"title": "Completed check", "status": "done"},
                {"title": "Private state", "status": "customer-token-123"},
            ],
        ),
    )

    report = build_recovery_report(
        _receipt("user-1"),
        [action, result],
        ConversationExecutionStatus.ERROR,
        mask_text=lambda text: text.replace("deployment", "[REDACTED]").replace(
            "customer-token-123", "[REDACTED]"
        ),
    )

    assert [task.model_dump() for task in report.incomplete_tasks] == [
        {"title": "Verify [REDACTED]", "status": "in_progress"},
        {"title": "Private state", "status": "[REDACTED]"},
    ]
    assert [item.action_event_id for item in report.uncertain_operations] == [action.id]
    assert report.uncertain_operations[0].reason == (
        "The command timed out while its process may still be running."
    )
    assert "deploy" not in report.model_dump_json()
    assert "customer-token-123" not in report.model_dump_json()


def test_recovery_report_is_partial_when_start_event_is_not_on_branch():
    error = ConversationErrorEvent(
        id="error-1",
        source="environment",
        code="MaxIterationsReached",
        detail="limit",
    )
    report = build_recovery_report(
        _receipt("missing-start"),
        [error],
        ConversationExecutionStatus.ERROR,
        evidence_coverage=EvidenceCoverage.PARTIAL,
        omitted_event_count=None,
    )
    assert report.evidence_coverage == EvidenceCoverage.PARTIAL
    assert report.omitted_event_count is None


def test_recovery_report_marks_unreadable_event_history_unavailable(
    tmp_path, monkeypatch
):
    conversation = _failing_conversation(tmp_path)
    try:
        conversation.send_message("Please inspect the parser.")
        receipt = begin_run(conversation._state)

        def unreadable_history():
            raise OSError("event history is unavailable")

        monkeypatch.setattr(
            conversation._state, "_resolve_active_leaf", unreadable_history
        )

        _, report = finish_run(
            conversation._state,
            receipt,
            reason_code="RuntimeError",
            outcome_override=RunOutcome.FAILED,
        )

        assert report.evidence_coverage == EvidenceCoverage.UNAVAILABLE
        assert report.omitted_event_count is None
        assert report.last_agent_update is None
        assert report.recent_activity == []
    finally:
        conversation.close()


class _FailingAgent(AgentBase):
    def step(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ) -> None:  # noqa: ARG002
        raise ValueError("private provider detail")


def _failing_conversation(tmp_path: Path) -> LocalConversation:
    agent = _FailingAgent(
        llm=LLM(model="gpt-4o-mini", api_key=None, usage_id="recovery-test"),
        tools=[],
    )
    return LocalConversation(
        agent=agent,
        workspace=tmp_path / "workspace",
        persistence_dir=tmp_path / "conversations",
        delete_on_close=False,
    )


def test_local_run_persists_report_without_copying_exception_detail(tmp_path: Path):
    conversation = _failing_conversation(tmp_path)
    try:
        conversation.send_message("Please inspect the parser.")
        with pytest.raises(ConversationRunError):
            conversation.run()

        report = conversation.get_recovery_report()
        assert report is not None
        assert report.outcome.value == "failed"
        assert report.reason_code == "ValueError"
        assert report.evidence_coverage == EvidenceCoverage.COMPLETE
        assert report.error_event_id is not None
        assert "private provider detail" not in report.model_dump_json()
        receipt = json.loads(conversation._state._fs.read("run_recovery/latest.json"))
        assert receipt["receipt"]["phase"] == "settled"
        error = next(
            event
            for event in conversation.state.events
            if isinstance(event, ConversationErrorEvent)
        )
        assert error.id == report.error_event_id

        first_run_id = report.run_id
        conversation.send_message("Please retry the parser check.")
        with pytest.raises(ConversationRunError):
            conversation.run()

        latest_report = conversation.get_recovery_report()
        assert latest_report is not None
        assert latest_report.run_id != first_run_id
        assert latest_report.error_event_id is not None
        assert latest_report.error_event_id != report.error_event_id
    finally:
        conversation.close()


def test_recovery_after_restart_settles_the_persisted_run_receipt(tmp_path: Path):
    original = _failing_conversation(tmp_path)
    conversation_id = original.id
    try:
        original.send_message("Please inspect the parser.")
        receipt = begin_run(original._state, owner_generation=3)
        original._state.transition(ConversationExecutionStatus.RUNNING, "test run")
        original._state.transition(
            ConversationExecutionStatus.ERROR, "simulate restart"
        )
        original.close()

        resumed = LocalConversation(
            agent=_FailingAgent(
                llm=LLM(model="gpt-4o-mini", api_key=None, usage_id="recovery-test"),
                tools=[],
            ),
            workspace=tmp_path / "workspace",
            persistence_dir=tmp_path / "conversations",
            conversation_id=conversation_id,
            delete_on_close=False,
        )
        try:
            resumed.set_recovery_owner_generation(4)
            report = resumed.recover_interrupted_run(allow_legacy_recovery=True)

            assert report is not None
            assert report.outcome.value == "interrupted_unknown"
            assert report.reason_code == "ServerRestarted"
            saved = json.loads(resumed._state._fs.read("run_recovery/latest.json"))
            assert saved["receipt"]["phase"] == "settled"
            assert saved["receipt"]["owner_generation"] == 4
            assert resumed.recover_interrupted_run(allow_legacy_recovery=True) == report
            with pytest.raises(RuntimeError, match="receipt was superseded"):
                finish_run(
                    resumed._state,
                    receipt,
                    owner_generation=3,
                    outcome_override=RunOutcome.FAILED,
                )
            assert resumed.get_recovery_report() == report
        finally:
            resumed.close()
    finally:
        original.close()


def test_restart_uses_running_receipt_when_state_is_not_error(tmp_path: Path):
    original = _failing_conversation(tmp_path)
    conversation_id = original.id
    try:
        original.send_message("Please inspect the parser.")
        begin_run(original._state, owner_generation=3)
        assert original._state.execution_status != ConversationExecutionStatus.ERROR
        original.close()

        resumed = LocalConversation(
            agent=_FailingAgent(
                llm=LLM(model="gpt-4o-mini", api_key=None, usage_id="recovery-test"),
                tools=[],
            ),
            workspace=tmp_path / "workspace",
            persistence_dir=tmp_path / "conversations",
            conversation_id=conversation_id,
            delete_on_close=False,
        )
        try:
            resumed.set_recovery_owner_generation(4)
            report = resumed.recover_interrupted_run(allow_legacy_recovery=True)

            assert report is not None
            assert report.outcome == RunOutcome.INTERRUPTED_UNKNOWN
            assert report.reason_code == "ServerRestarted"
        finally:
            resumed.close()
    finally:
        original.close()


@pytest.mark.asyncio
async def test_async_local_run_persists_recovery_report(tmp_path: Path):
    conversation = _failing_conversation(tmp_path)
    try:
        with pytest.raises(ConversationRunError):
            await conversation.arun()

        report = conversation.get_recovery_report()
        assert report is not None
        assert report.outcome.value == "failed"
        assert report.run_id is not None
    finally:
        conversation.close()
