import uuid

from pydantic import SecretStr

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.conversation.types import (
    ConversationCallbackType,
    ConversationTokenCallbackType,
)
from openhands.sdk.event.llm_convertible import SystemPromptEvent
from openhands.sdk.llm import LLM, TextContent
from openhands.sdk.security.confirmation_policy import AlwaysConfirm, NeverConfirm


class ConversationIdDummyAgent(AgentBase):
    def __init__(self):
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        super().__init__(llm=llm, tools=[])

    def init_state(
        self, state: ConversationState, on_event: ConversationCallbackType
    ) -> None:
        event = SystemPromptEvent(
            source="agent", system_prompt=TextContent(text="dummy"), tools=[]
        )
        on_event(event)

    def step(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ) -> None:
        pass


def test_conversation_has_unique_id():
    """Test that each conversation gets a unique UUID."""
    agent = ConversationIdDummyAgent()
    conversation = Conversation(agent=agent)

    # Check that id exists and is a UUID
    assert hasattr(conversation, "id")
    assert isinstance(conversation.id, uuid.UUID)


def test_conversation_ids_are_unique():
    """Test that different conversations get different IDs."""
    agent1 = ConversationIdDummyAgent()
    agent2 = ConversationIdDummyAgent()

    conversation1 = Conversation(agent=agent1)
    conversation2 = Conversation(agent=agent2)

    # Check that the IDs are different
    assert conversation1.id != conversation2.id

    # Check that both are UUIDs
    assert isinstance(conversation1.id, uuid.UUID)
    assert isinstance(conversation2.id, uuid.UUID)


def test_conversation_id_persists():
    """Test that the conversation ID doesn't change during the conversation lifecycle."""  # noqa: E501
    agent = ConversationIdDummyAgent()
    conversation = Conversation(agent=agent)

    original_id = conversation.id

    # Perform some operations that might affect the conversation
    conversation.set_confirmation_policy(AlwaysConfirm())
    conversation.set_confirmation_policy(NeverConfirm())

    # Check that the ID hasn't changed
    assert conversation.id == original_id
