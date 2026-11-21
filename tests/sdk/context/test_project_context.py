from pathlib import Path

import pytest
from pydantic import ValidationError

from openhands.sdk import Agent, ConversationExecutionStatus, LocalConversation
from openhands.sdk.context import AgentContext, ProjectContextProfile
from openhands.sdk.event import SystemPromptEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.testing import TestLLM


def test_agents_receive_only_their_selected_project_context(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Use the shared repository conventions.")
    profile_path = tmp_path / "project-context.yaml"
    profile_path.write_text(
        "sections:\n"
        "  architecture:\n"
        "    style: layered architecture\n"
        "  implementation:\n"
        "    path: services/catalog\n"
        "  testing:\n"
        "    command: pytest tests/catalog\n"
    )
    profile = ProjectContextProfile.from_file(profile_path)

    for section, expected, excluded in (
        ("implementation", "services/catalog", "pytest tests/catalog"),
        ("testing", "pytest tests/catalog", "services/catalog"),
    ):
        context = AgentContext(
            load_project_skills=True,
            skills=[profile.to_skill("architecture", section)],
        )
        agent = Agent(
            llm=TestLLM.from_messages(
                [Message(role="assistant", content=[TextContent(text="Done")])]
            ),
            tools=[],
            include_default_tools=[],
            agent_context=context,
        )
        conversation = LocalConversation(
            agent=agent,
            workspace=tmp_path,
            persistence_dir=tmp_path / section,
            delete_on_close=True,
        )
        try:
            conversation.send_message("Work on the catalog.")
            conversation.run()
            assert conversation.state.execution_status == (
                ConversationExecutionStatus.FINISHED
            )
            prompt = next(
                event
                for event in conversation.state.events
                if isinstance(event, SystemPromptEvent)
            )
            assert prompt.dynamic_context is not None
            assert (
                "Use the shared repository conventions." in prompt.dynamic_context.text
            )
            assert "layered architecture" in prompt.dynamic_context.text
            assert expected in prompt.dynamic_context.text
            assert excluded not in prompt.dynamic_context.text
        finally:
            conversation.close()


def test_profile_rejects_an_invalid_sections_mapping(tmp_path: Path) -> None:
    profile_path = tmp_path / "project-context.yaml"
    profile_path.write_text("sections: [testing]")
    with pytest.raises(ValidationError, match="sections"):
        ProjectContextProfile.from_file(profile_path)


@pytest.mark.parametrize(
    ("section_names", "error"),
    [
        ((), "Select at least one project context section"),
        (("typo",), "Unknown project context section: 'typo'"),
    ],
)
def test_profile_rejects_invalid_section_selection(
    section_names: tuple[str, ...], error: str
) -> None:
    profile = ProjectContextProfile(sections={"testing": {"command": "pytest"}})
    with pytest.raises(ValueError, match=error):
        profile.to_skill(*section_names)
