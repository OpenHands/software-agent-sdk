from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from openhands.sdk import Agent, LocalConversation, Tool
from openhands.sdk.testing import TestLLM


@pytest.fixture
def history_conversation_factory(
    tmp_path: Path,
) -> Iterator[Callable[..., LocalConversation]]:
    conversations: list[LocalConversation] = []

    def create(agent: Agent | None = None) -> LocalConversation:
        conversation = LocalConversation(
            agent=agent
            or Agent(
                llm=TestLLM.from_messages([]),
                tools=[Tool(name="ConversationHistoryTool")],
            ),
            workspace=tmp_path,
            persistence_dir=tmp_path / "state",
            visualizer=None,
        )
        conversations.append(conversation)
        return conversation

    yield create
    for conversation in conversations:
        conversation.close()
