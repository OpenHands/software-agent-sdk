"""Integration-like tests documenting LocalConversation restore semantics.

These tests aim to be a behavioral spec for conversation restore:

- Normal lifecycle: start -> send/run -> send/run -> close -> restore -> send/run
- Restore MUST fail if the agent toolset changes (tools are part of the system prompt)
- Restore MUST succeed if other agent configuration changes (LLM, condenser, skills)
"""

from __future__ import annotations

import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.sdk import Agent
from openhands.sdk.context import AgentContext, KeywordTrigger, Skill
from openhands.sdk.context.condenser.llm_summarizing_condenser import (
    LLMSummarizingCondenser,
)
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.llm import LLM
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


register_tool("TerminalTool", TerminalTool)
register_tool("FileEditorTool", FileEditorTool)


@dataclass
class RestoreLifecycle:
    """Reusable harness that exercises the persistence/restore lifecycle."""

    workspace_dir: Path
    persistence_base_dir: Path
    conversation_id: uuid.UUID | None = None

    def create_conversation(self, agent: Agent) -> LocalConversation:
        return LocalConversation(
            agent=agent,
            workspace=self.workspace_dir,
            persistence_dir=self.persistence_base_dir,
            conversation_id=self.conversation_id,
            visualizer=None,
        )

    def send_and_run(self, conversation: LocalConversation, message: str) -> None:
        conversation.send_message(message)
        conversation.run()

    def run_initial_session(self, agent: Agent) -> dict[str, Any]:
        conversation = self.create_conversation(agent)
        try:
            self.conversation_id = conversation.id
            self.send_and_run(conversation, "First message")
            self.send_and_run(conversation, "Second message")

            return {
                "conversation_id": conversation.id,
                "event_count": len(conversation.state.events),
                "state_dump_excluding_events": conversation._state.model_dump(
                    mode="json", exclude={"events"}
                ),
            }
        finally:
            conversation.close()

    def restore(self, agent: Agent) -> LocalConversation:
        assert self.conversation_id is not None, "Call run_initial_session() first"
        return self.create_conversation(agent)


def _agent(
    *,
    llm_model: str,
    tools: list[Tool],
    condenser_max_size: int,
    skill_name: str,
    skill_keyword: str,
) -> Agent:
    llm = LLM(model=llm_model, api_key=SecretStr("test-key"), usage_id="test-llm")
    condenser = LLMSummarizingCondenser(
        llm=llm,
        max_size=condenser_max_size,
        keep_first=2,
    )

    ctx = AgentContext(
        skills=[
            Skill(
                name=skill_name,
                content=f"Skill content for {skill_name}",
                trigger=KeywordTrigger(keywords=[skill_keyword]),
            )
        ]
    )

    return Agent(llm=llm, tools=tools, condenser=condenser, agent_context=ctx)


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_conversation_restore_lifecycle_happy_path(mock_completion):
    """Baseline: restore should load prior events and allow further execution."""

    from tests.conftest import create_mock_litellm_response

    mock_completion.return_value = create_mock_litellm_response(
        content="I'll help you with that.", finish_reason="stop"
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        lifecycle = RestoreLifecycle(
            workspace_dir=base / "workspace",
            persistence_base_dir=base / "persist",
        )
        lifecycle.workspace_dir.mkdir(parents=True, exist_ok=True)
        lifecycle.persistence_base_dir.mkdir(parents=True, exist_ok=True)

        tools = [Tool(name="TerminalTool"), Tool(name="FileEditorTool")]
        agent = _agent(
            llm_model="gpt-4o-mini",
            tools=tools,
            condenser_max_size=80,
            skill_name="skill-v1",
            skill_keyword="alpha",
        )

        initial = lifecycle.run_initial_session(agent)

        restored = lifecycle.restore(agent)
        try:
            assert restored.id == initial["conversation_id"]
            assert len(restored.state.events) == initial["event_count"]

            lifecycle.send_and_run(restored, "Third message")
            assert len(restored.state.events) > initial["event_count"]
        finally:
            restored.close()


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_conversation_restore_fails_when_removing_tools(mock_completion):
    """Restore must fail when runtime tools remove a persisted tool."""

    from tests.conftest import create_mock_litellm_response

    mock_completion.return_value = create_mock_litellm_response(
        content="I'll help you with that.", finish_reason="stop"
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        lifecycle = RestoreLifecycle(
            workspace_dir=base / "workspace",
            persistence_base_dir=base / "persist",
        )
        lifecycle.workspace_dir.mkdir(parents=True, exist_ok=True)
        lifecycle.persistence_base_dir.mkdir(parents=True, exist_ok=True)

        persisted_tools = [Tool(name="TerminalTool"), Tool(name="FileEditorTool")]
        persisted_agent = _agent(
            llm_model="gpt-4o-mini",
            tools=persisted_tools,
            condenser_max_size=80,
            skill_name="skill-v1",
            skill_keyword="alpha",
        )
        lifecycle.run_initial_session(persisted_agent)

        runtime_agent = _agent(
            llm_model="gpt-4o-mini",
            tools=[Tool(name="TerminalTool")],
            condenser_max_size=80,
            skill_name="skill-v1",
            skill_keyword="alpha",
        )

        with pytest.raises(
            ValueError, match="tools cannot be changed mid-conversation"
        ) as exc:
            restored = lifecycle.restore(runtime_agent)
            restored.close()

        assert "removed:" in str(exc.value)
        assert "FileEditorTool" in str(exc.value)


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_conversation_restore_fails_when_adding_tools(mock_completion):
    """Restore must fail when runtime tools add a new tool."""

    from tests.conftest import create_mock_litellm_response

    mock_completion.return_value = create_mock_litellm_response(
        content="I'll help you with that.", finish_reason="stop"
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        lifecycle = RestoreLifecycle(
            workspace_dir=base / "workspace",
            persistence_base_dir=base / "persist",
        )
        lifecycle.workspace_dir.mkdir(parents=True, exist_ok=True)
        lifecycle.persistence_base_dir.mkdir(parents=True, exist_ok=True)

        persisted_tools = [Tool(name="TerminalTool")]
        persisted_agent = _agent(
            llm_model="gpt-4o-mini",
            tools=persisted_tools,
            condenser_max_size=80,
            skill_name="skill-v1",
            skill_keyword="alpha",
        )
        lifecycle.run_initial_session(persisted_agent)

        runtime_agent = _agent(
            llm_model="gpt-4o-mini",
            tools=[Tool(name="TerminalTool"), Tool(name="FileEditorTool")],
            condenser_max_size=80,
            skill_name="skill-v1",
            skill_keyword="alpha",
        )

        with pytest.raises(
            ValueError, match="tools cannot be changed mid-conversation"
        ) as exc:
            restored = lifecycle.restore(runtime_agent)
            restored.close()

        assert "added:" in str(exc.value)
        assert "FileEditorTool" in str(exc.value)


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_conversation_restore_succeeds_when_llm_condenser_and_skills_change(
    mock_completion,
):
    """Restore should succeed when ONLY non-breaking agent config changes."""

    from tests.conftest import create_mock_litellm_response

    mock_completion.return_value = create_mock_litellm_response(
        content="Acknowledged.", finish_reason="stop"
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        lifecycle = RestoreLifecycle(
            workspace_dir=base / "workspace",
            persistence_base_dir=base / "persist",
        )
        lifecycle.workspace_dir.mkdir(parents=True, exist_ok=True)
        lifecycle.persistence_base_dir.mkdir(parents=True, exist_ok=True)

        tools = [Tool(name="TerminalTool"), Tool(name="FileEditorTool")]

        persisted_agent = _agent(
            llm_model="gpt-4o-mini",
            tools=tools,
            condenser_max_size=80,
            skill_name="skill-v1",
            skill_keyword="alpha",
        )
        initial = lifecycle.run_initial_session(persisted_agent)

        runtime_agent = _agent(
            llm_model="gpt-4o",
            tools=tools,
            condenser_max_size=120,
            skill_name="skill-v2",
            skill_keyword="beta",
        )

        restored = lifecycle.restore(runtime_agent)
        try:
            assert restored.id == initial["conversation_id"]
            assert len(restored.state.events) == initial["event_count"]

            assert restored.agent.llm.model == "gpt-4o"
            assert isinstance(restored.agent.condenser, LLMSummarizingCondenser)
            assert restored.agent.condenser.max_size == 120

            restored.send_message("beta: please use the new skill")
            last_event = restored.state.events[-1]
            assert getattr(last_event, "source", None) == "user"
            assert getattr(last_event, "activated_skills", []) == ["skill-v2"]

            restored.run()
            assert len(restored.state.events) > initial["event_count"]
        finally:
            restored.close()
