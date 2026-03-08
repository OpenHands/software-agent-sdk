from __future__ import annotations

from pathlib import Path

from openhands.sdk.agent import Agent
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.context.skills import load_project_skills
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import SystemPromptEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.testing import TestLLM


def test_system_prompt_includes_repo_root_agents_md_when_workdir_is_subdir(
    tmp_path: Path,
):
    """Repo-root AGENTS.md should still be injected when starting from a subdir.

    This is the integration-style equivalent of the CLI manual test:
    - work_dir is a subdirectory
    - git repo root contains AGENTS.md
    - AgentContext is built from load_project_skills(work_dir)
    - LocalConversation initialization emits a SystemPromptEvent

    We assert the sentinel from the repo root AGENTS.md appears in the
    SystemPromptEvent.dynamic_context.
    """

    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("# Project Guidelines\n\nSENTINEL_ROOT_123\n")

    subdir = tmp_path / "subdir"
    subdir.mkdir()

    skills = load_project_skills(subdir)
    ctx = AgentContext(
        skills=skills,
        # Keep deterministic across environments.
        current_datetime="2026-01-01T00:00:00Z",
    )

    agent = Agent(
        llm=TestLLM.from_messages(
            [
                Message(
                    role="assistant",
                    content=[TextContent(text="ok")],
                )
            ],
            model="test-model",
        ),
        tools=[],
        include_default_tools=[],
        agent_context=ctx,
    )

    conversation = LocalConversation(
        agent=agent,
        workspace=subdir,
        persistence_dir=tmp_path / "conversation",
        delete_on_close=True,
    )
    conversation.send_message("hi")

    system_prompt_event = next(
        e for e in conversation.state.events if isinstance(e, SystemPromptEvent)
    )
    assert system_prompt_event.dynamic_context is not None
    assert "SENTINEL_ROOT_123" in system_prompt_event.dynamic_context.text

    conversation.close()
