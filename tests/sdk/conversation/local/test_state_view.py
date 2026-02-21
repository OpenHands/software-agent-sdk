"""Test that ConversationState maintains a View that stays in sync with events."""

import tempfile
import uuid

from pydantic import SecretStr

from openhands.sdk import Agent, Conversation
from openhands.sdk.context.view import View
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event.llm_convertible import MessageEvent, SystemPromptEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.workspace import LocalWorkspace


def test_fresh_state_has_empty_view():
    """A brand-new ConversationState should have an empty View."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    state = ConversationState.create(
        agent=agent,
        id=uuid.uuid4(),
        workspace=LocalWorkspace(working_dir="/tmp"),
    )
    assert isinstance(state.view, View)
    assert len(state.view) == 0


def test_view_not_serialized():
    """View should not appear in serialized state."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    state = ConversationState.create(
        agent=agent,
        id=uuid.uuid4(),
        workspace=LocalWorkspace(working_dir="/tmp"),
    )
    dumped = state.model_dump()
    assert "view" not in dumped
    assert "_view" not in dumped


def test_view_updated_via_default_callback():
    """Events emitted through the conversation callback should update the view."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    conversation = Conversation(agent=agent, visualizer=None)
    assert isinstance(conversation, LocalConversation)

    conversation.send_message(Message(role="user", content=[TextContent(text="hello")]))

    # The view should contain the same LLM-convertible events as from_events
    expected_view = View.from_events(conversation.state.events)
    assert len(conversation.state.view) == len(expected_view)
    assert [e.id for e in conversation.state.view.events] == [
        e.id for e in expected_view.events
    ]


def test_view_repopulated_on_resume():
    """When resuming a conversation, the view should be rebuilt from events."""
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini",
            api_key=SecretStr("test-key"),
            usage_id="test-llm",
        )
        agent = Agent(llm=llm, tools=[])

        conv_id = uuid.uuid4()
        persist_path = LocalConversation.get_persistence_dir(temp_dir, conv_id)
        state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
        )

        # Add events directly to the event log (simulating persisted events)
        event1 = SystemPromptEvent(
            source="agent",
            system_prompt=TextContent(text="system"),
            tools=[],
        )
        event2 = MessageEvent(
            source="user",
            llm_message=Message(role="user", content=[TextContent(text="hello")]),
        )
        state.events.append(event1)
        state.events.append(event2)

        # Resume the conversation
        resumed = Conversation(
            agent=agent,
            persistence_dir=temp_dir,
            workspace=LocalWorkspace(working_dir="/tmp"),
            conversation_id=conv_id,
            visualizer=None,
        )
        assert isinstance(resumed, LocalConversation)

        # The view should be populated from the persisted events
        expected_view = View.from_events(resumed.state.events)
        assert len(resumed.state.view) == len(expected_view)
        assert len(resumed.state.view) > 0
        assert [e.id for e in resumed.state.view.events] == [
            e.id for e in expected_view.events
        ]
