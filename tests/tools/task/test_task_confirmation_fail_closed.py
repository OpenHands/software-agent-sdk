"""Tests for fail-closed sub-agent confirmation & analyzer inheritance (#6999)."""

import json
from pathlib import Path
from typing import Any

import pytest

from openhands.sdk import Agent, LocalConversation, Message, TextContent, Tool
from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.event import (
    ActionEvent,
    Event,
    SecurityAnalysisEvent,
    UserRejectObservation,
)
from openhands.sdk.io import LocalFileStore
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.confirmation_policy import (
    AlwaysConfirm,
    ConfirmRisky,
    NeverConfirm,
)
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.subagent import AgentDefinition
from openhands.sdk.subagent.registry import (
    _reset_registry_for_tests,
    agent_definition_to_factory,
    register_agent,
)
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import register_tool
from openhands.tools.preset import register_builtins_agents
from openhands.tools.preset.default import register_default_tools
from openhands.tools.task import TaskToolSet
from openhands.tools.task.definition import TaskAction


class CommandKeywordAnalyzer(SecurityAnalyzerBase):
    """Evaluates commands containing 'DANGER' as HIGH risk, others as LOW risk."""

    marker: str = "DANGER"

    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        cmd = getattr(action.action, "command", "")
        if self.marker in cmd:
            return SecurityRisk.HIGH
        return SecurityRisk.LOW


@pytest.fixture(autouse=True)
def _clean_registry():
    _reset_registry_for_tests()
    register_default_tools(enable_browser=False)
    register_tool("TaskToolSet", TaskToolSet)
    register_builtins_agents(enable_browser=False)
    yield
    _reset_registry_for_tests()


def _cmd_msg(call_id: str, command: str) -> Message:
    return Message(
        role="assistant",
        content=[TextContent(text="")],
        tool_calls=[
            MessageToolCall(
                id=call_id,
                name="terminal",
                arguments=json.dumps({"command": command}),
                origin="completion",
            )
        ],
    )


def _text_msg(text: str) -> Message:
    return Message(role="assistant", content=[TextContent(text=text)])


def _load_persisted_subagent_events(parent: LocalConversation) -> list[Event]:
    assert parent.state.persistence_dir is not None
    subagents_root = Path(parent.state.persistence_dir) / "subagents"
    conv_dirs = [p for p in subagents_root.iterdir() if p.is_dir()]
    assert len(conv_dirs) == 1
    return list(EventLog(LocalFileStore(str(conv_dirs[0]))))


def test_task_toolset_fails_closed_under_always_confirm_without_handler(
    tmp_path: Path,
) -> None:
    """With confirmation_handler=None, AlwaysConfirm sub-agent actions are rejected
    via reject_pending_actions with a reason stating interactive confirmation is
    unavailable, and side effects do not occur.
    """
    canary = tmp_path / "executed_without_confirmation.txt"
    persist_dir = tmp_path / "persist"
    llm = TestLLM.from_messages(
        [
            _cmd_msg("call_sub_1", f"echo PWNED > {canary}"),
            _text_msg("Command was rejected because confirmation is required."),
        ]
    )
    parent = LocalConversation(
        agent=Agent(llm=llm, tools=[Tool(name="TaskToolSet"), Tool(name="terminal")]),
        workspace=str(tmp_path),
        persistence_dir=str(persist_dir),
        visualizer=None,
    )
    parent.set_confirmation_policy(AlwaysConfirm())

    task_tool = TaskToolSet.create(conv_state=parent.state)[0]
    obs = task_tool(
        TaskAction(prompt="Run command", subagent_type="bash-runner"),
        parent,
    )
    events = _load_persisted_subagent_events(parent)
    parent.close()

    assert not canary.exists(), "Sub-agent executed command despite AlwaysConfirm!"
    assert obs.status == "completed"

    rejections = [ev for ev in events if isinstance(ev, UserRejectObservation)]
    assert len(rejections) == 1
    assert "interactive confirmation" in rejections[0].rejection_reason.lower()
    assert "unavailable" in rejections[0].rejection_reason.lower()


def test_confirm_risky_inherits_parent_security_analyzer_on_create_and_resume(
    tmp_path: Path,
) -> None:
    """TaskManager propagates parent.state.security_analyzer on both task creation
    and task resume; LOW-risk commands execute without confirmation, HIGH-risk
    commands are rejected when handler is None, and SecurityAnalysisEvent records
    evaluated LOW/HIGH risks rather than UNKNOWN.
    """
    safe_file = tmp_path / "safe_low_risk.txt"
    danger_create = tmp_path / "danger_create.txt"
    danger_resume = tmp_path / "danger_resume.txt"
    persist_dir = tmp_path / "persist"

    llm = TestLLM.from_messages(
        [
            _cmd_msg("call_safe", f"echo SAFE > {safe_file}"),
            _cmd_msg("call_high_1", f"echo DANGER > {danger_create}"),
            _text_msg("Created task finished."),
            _cmd_msg("call_high_2", f"echo DANGER > {danger_resume}"),
            _text_msg("Resumed task finished."),
        ]
    )
    analyzer = CommandKeywordAnalyzer(marker="DANGER")
    parent = LocalConversation(
        agent=Agent(llm=llm, tools=[Tool(name="TaskToolSet"), Tool(name="terminal")]),
        workspace=str(tmp_path),
        persistence_dir=str(persist_dir),
        visualizer=None,
    )
    parent.set_security_analyzer(analyzer)
    parent.set_confirmation_policy(ConfirmRisky(threshold=SecurityRisk.HIGH))

    task_tool = TaskToolSet.create(conv_state=parent.state)[0]
    manager = task_tool.executor._manager

    # Inspect analyzer equality directly on created and resumed sub-conversations
    seen_analyzers: list[SecurityAnalyzerBase | None] = []
    orig_run_until_finished = manager._run_until_finished

    def wrapped_run_until_finished(
        task_id: str, conversation: LocalConversation
    ) -> None:
        seen_analyzers.append(conversation.state.security_analyzer)
        orig_run_until_finished(task_id, conversation)

    manager._run_until_finished = wrapped_run_until_finished  # type: ignore[method-assign]

    obs1 = task_tool(
        TaskAction(prompt="Step 1", subagent_type="bash-runner"),
        parent,
    )
    assert safe_file.exists()
    assert not danger_create.exists()
    assert obs1.task_id == "task_00000001"

    obs2 = task_tool(
        TaskAction(
            prompt="Step 2",
            subagent_type="bash-runner",
            resume="task_00000001",
        ),
        parent,
    )
    events = _load_persisted_subagent_events(parent)
    expected_analyzer = parent.state.security_analyzer
    parent.close()

    assert not danger_resume.exists()
    assert obs2.status == "completed"
    assert len(seen_analyzers) == 2
    assert seen_analyzers[0] == expected_analyzer
    assert seen_analyzers[1] == expected_analyzer

    analysis_events = [ev for ev in events if isinstance(ev, SecurityAnalysisEvent)]
    assert len(analysis_events) == 3
    recorded_risks = [risk for ev in analysis_events for risk in ev.risks.values()]
    assert recorded_risks == [
        SecurityRisk.LOW,
        SecurityRisk.HIGH,
        SecurityRisk.HIGH,
    ]
    rejections = [ev for ev in events if isinstance(ev, UserRejectObservation)]
    assert len(rejections) == 2


def test_explicit_always_confirm_subagent_fails_closed_when_parent_never_confirm(
    tmp_path: Path,
) -> None:
    """Sub-agent definition with permission_mode='always_confirm' fails closed
    when confirmation_handler is None, even if parent uses NeverConfirm.
    """
    agent_def = AgentDefinition(
        name="strict-subagent",
        description="Requires confirmation",
        tools=["terminal"],
        permission_mode="always_confirm",
    )
    register_agent(
        name="strict-subagent",
        factory_func=agent_definition_to_factory(agent_def),
        description=agent_def,
    )

    canary = tmp_path / "strict_canary.txt"
    persist_dir = tmp_path / "persist"
    llm = TestLLM.from_messages(
        [
            _cmd_msg("call_strict", f"echo PWNED > {canary}"),
            _text_msg("Blocked by strict-subagent policy."),
        ]
    )
    parent = LocalConversation(
        agent=Agent(llm=llm, tools=[Tool(name="TaskToolSet"), Tool(name="terminal")]),
        workspace=str(tmp_path),
        persistence_dir=str(persist_dir),
        visualizer=None,
    )
    parent.set_confirmation_policy(NeverConfirm())

    task_tool = TaskToolSet.create(conv_state=parent.state)[0]
    task_tool(TaskAction(prompt="Try command", subagent_type="strict-subagent"), parent)
    events = _load_persisted_subagent_events(parent)
    parent.close()

    assert not canary.exists()
    rejections = [ev for ev in events if isinstance(ev, UserRejectObservation)]
    assert len(rejections) == 1
    assert "interactive confirmation" in rejections[0].rejection_reason.lower()


@pytest.mark.parametrize("approve", [True, False])
def test_explicit_confirmation_handler_approves_or_rejects(
    tmp_path: Path, approve: bool
) -> None:
    """Explicit confirmation_handler returning True executes pending actions and
    returning False rejects them with 'User rejected the actions'.
    """
    canary = tmp_path / f"handler_{approve}.txt"
    persist_dir = tmp_path / "persist"
    seen_calls: list[tuple[str, int]] = []

    def handler(task_id: str, pending: list[Any]) -> bool:
        seen_calls.append((task_id, len(pending)))
        return approve

    llm = TestLLM.from_messages(
        [
            _cmd_msg("call_h1", f"echo OK > {canary}"),
            _text_msg("Finished."),
        ]
    )
    parent = LocalConversation(
        agent=Agent(llm=llm, tools=[Tool(name="TaskToolSet"), Tool(name="terminal")]),
        workspace=str(tmp_path),
        persistence_dir=str(persist_dir),
        visualizer=None,
    )
    parent.set_confirmation_policy(AlwaysConfirm())

    task_tool = TaskToolSet.create(
        conv_state=parent.state, confirmation_handler=handler
    )[0]
    task_tool(TaskAction(prompt="Run", subagent_type="bash-runner"), parent)
    events = _load_persisted_subagent_events(parent)
    parent.close()

    assert seen_calls == [("task_00000001", 1)]
    assert canary.exists() is approve

    rejections = [ev for ev in events if isinstance(ev, UserRejectObservation)]
    if approve:
        assert rejections == []
    else:
        assert len(rejections) == 1
        assert rejections[0].rejection_reason == "User rejected the actions"


def test_never_confirm_subagent_executes_normally(tmp_path: Path) -> None:
    """NeverConfirm sub-agents do not wait for confirmation and finish normally."""
    marker = tmp_path / "normal_ok.txt"
    llm = TestLLM.from_messages(
        [
            _cmd_msg("call_ok", f"echo NORMAL > {marker}"),
            _text_msg("Done."),
        ]
    )
    parent = LocalConversation(
        agent=Agent(llm=llm, tools=[Tool(name="TaskToolSet"), Tool(name="terminal")]),
        workspace=str(tmp_path),
        visualizer=None,
    )
    parent.set_confirmation_policy(NeverConfirm())

    task_tool = TaskToolSet.create(conv_state=parent.state)[0]
    task_tool(TaskAction(prompt="Run", subagent_type="bash-runner"), parent)
    parent.close()

    assert marker.exists()
    assert marker.read_text().strip() == "NORMAL"
