from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.task_outcome import TaskOutcome
from openhands.sdk.tool.builtins import (
    BUILT_IN_TOOL_CLASSES,
    BUILT_IN_TOOLS,
    ReportTaskOutcomeAction,
    ReportTaskOutcomeObservation,
    ReportTaskOutcomeTool,
)
from openhands.sdk.workspace.local import LocalWorkspace


def _state(tmp_path) -> ConversationState:
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test")
    return ConversationState.create(
        id=uuid.uuid4(),
        agent=Agent(llm=llm, tools=[]),
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
        persistence_dir=str(tmp_path / "conversation"),
    )


def _tool() -> ReportTaskOutcomeTool:
    (tool,) = ReportTaskOutcomeTool.create()
    return tool


def _run(action: ReportTaskOutcomeAction, conv: Any) -> ReportTaskOutcomeObservation:
    executor = _tool().executor
    assert executor is not None
    return executor(action, conversation=conv)


def test_in_default_builtins_and_resolvable_by_name():
    assert ReportTaskOutcomeTool in BUILT_IN_TOOLS
    assert BUILT_IN_TOOL_CLASSES["ReportTaskOutcomeTool"] is ReportTaskOutcomeTool
    assert ReportTaskOutcomeTool.name == "report_task_outcome"


def test_report_task_outcome_records_latest_outcome(tmp_path):
    state = _state(tmp_path)
    conv = SimpleNamespace(state=state)

    obs = _run(
        ReportTaskOutcomeAction(
            status="partial_success",
            summary="Implemented code, tests still pending.",
            blockers=[{"type": "verification", "message": "Tests not run yet."}],
            artifacts=[{"type": "file", "path": "src/example.py"}],
            confidence=0.7,
            needs_user_action=False,
            final=False,
        ),
        conv,
    )

    assert obs.is_error is False
    assert state.task_outcome is not None
    assert state.task_outcome.status == "partial_success"
    assert state.task_outcome.blockers[0].type == "verification"
    assert state.task_outcome.artifacts[0].path == "src/example.py"
    assert state.task_outcome.reported_at is not None

    restored = ConversationState.create(
        id=state.id,
        agent=state.agent,
        workspace=state.workspace,
        persistence_dir=str(tmp_path / "conversation"),
    )
    assert restored.task_outcome is not None
    assert restored.task_outcome.status == "partial_success"


def test_latest_report_wins(tmp_path):
    state = _state(tmp_path)
    conv = SimpleNamespace(state=state)

    _run(ReportTaskOutcomeAction(status="blocked", summary="Waiting."), conv)
    _run(
        ReportTaskOutcomeAction(
            status="success",
            summary="Completed after retry.",
            final=True,
            confidence=0.95,
        ),
        conv,
    )

    assert state.task_outcome is not None
    assert state.task_outcome.status == "success"
    assert state.task_outcome.final is True
    assert state.task_outcome.summary == "Completed after retry."


def test_error_without_conversation_does_not_persist():
    obs = _run(
        ReportTaskOutcomeAction(status="failed", summary="No conversation."),
        conv=None,
    )

    assert obs.is_error is True
    assert isinstance(obs.outcome, TaskOutcome)
    assert obs.outcome.status == "failed"
    assert obs.outcome.blockers[0].type == "runtime_error"
