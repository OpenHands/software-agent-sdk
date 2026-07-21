"""LocalConversation lazily resolves persistent memory (``AgentContext.load_memory``).

Memory resolution mirrors project skills: AgentContext cannot read the
workspace at validation time, so LocalConversation loads the MEMORY.md indexes
on the first ``send_message()`` and the result lands in the SystemPromptEvent.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from openhands.sdk.agent import Agent
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.context.memory import MEMORY_INDEX_RELPATH
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import SystemPromptEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.testing import TestLLM


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Keep the user memory tier (``~/.openhands/memory/``) off the host home."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def _agent(agent_context: AgentContext) -> Agent:
    return Agent(
        llm=TestLLM.from_messages(
            [Message(role="assistant", content=[TextContent(text="ok")])],
            model="test-model",
        ),
        tools=[],
        include_default_tools=[],
        agent_context=agent_context,
    )


def _write_memory_index(workspace: Path, text: str) -> None:
    index = workspace / MEMORY_INDEX_RELPATH
    index.parent.mkdir(parents=True)
    index.write_text(text)


def _system_prompt_event(
    tmp_path: Path, agent_context: AgentContext
) -> SystemPromptEvent:
    conversation = LocalConversation(
        agent=_agent(agent_context),
        workspace=tmp_path / "workspace",
        persistence_dir=tmp_path / "conversation",
        delete_on_close=True,
    )
    try:
        conversation.send_message("hi")
        return next(
            e for e in conversation.state.events if isinstance(e, SystemPromptEvent)
        )
    finally:
        conversation.close()


def test_load_memory_flag_injects_memory_into_system_prompt(tmp_path: Path):
    workspace = tmp_path / "workspace"
    _write_memory_index(workspace, "- SENTINEL_MEMORY_789\n")

    event = _system_prompt_event(tmp_path, AgentContext(load_memory=True))

    assert event.dynamic_context is not None
    assert "<MEMORY_CONTEXT>" in event.dynamic_context.text
    assert "SENTINEL_MEMORY_789" in event.dynamic_context.text
    assert "persistent memory that survives across sessions" in event.system_prompt.text


def test_load_memory_flag_off_ignores_memory_files(tmp_path: Path):
    workspace = tmp_path / "workspace"
    _write_memory_index(workspace, "- SENTINEL_MEMORY_789\n")

    event = _system_prompt_event(tmp_path, AgentContext())

    dynamic = event.dynamic_context.text if event.dynamic_context else ""
    assert "SENTINEL_MEMORY_789" not in dynamic
    assert "<MEMORY_CONTEXT>" not in dynamic
    assert "Use `AGENTS.md` under the repository root" in event.system_prompt.text


def test_load_memory_without_memory_files_is_noop(tmp_path: Path):
    (tmp_path / "workspace").mkdir()

    event = _system_prompt_event(tmp_path, AgentContext(load_memory=True))

    dynamic = event.dynamic_context.text if event.dynamic_context else ""
    assert "<MEMORY_CONTEXT>" not in dynamic


def test_load_memory_failure_does_not_prevent_startup(tmp_path: Path):
    workspace = tmp_path / "workspace"
    _write_memory_index(workspace, "- SENTINEL_MEMORY_789\n")

    with patch(
        "openhands.sdk.conversation.impl.local_conversation.load_memory",
        side_effect=OSError("disk error"),
    ):
        event = _system_prompt_event(tmp_path, AgentContext(load_memory=True))

    dynamic = event.dynamic_context.text if event.dynamic_context else ""
    assert "<MEMORY_CONTEXT>" not in dynamic
