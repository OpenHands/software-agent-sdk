"""Tests for Agent.init_state modifying state in-place."""

import uuid

from openhands.sdk import LLM, Conversation
from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event.llm_convertible.system import SystemPromptEvent
from openhands.sdk.workspace import LocalWorkspace


def test_init_state_modifies_state_in_place(tmp_path):
    """Test that init_state modifies the passed state object in-place.

    This test verifies that when Agent.init_state() is called with a state
    and on_event callback, the SystemPromptEvent is appended to the same
    state object that was passed in (in-place modification).
    """
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    workspace = LocalWorkspace(working_dir=str(tmp_path))

    state = ConversationState.create(
        id=uuid.uuid4(),
        workspace=workspace,
        agent=agent,
    )

    original_state_id = id(state)
    original_events_id = id(state.events)
    initial_event_count = len(list(state.events))
    assert initial_event_count == 0, "State should start with no events"

    events_collected = []

    def on_event(event):
        events_collected.append(event)
        state.events.append(event)

    agent.init_state(state, on_event=on_event)

    assert id(state) == original_state_id, "State object identity should be preserved"
    assert id(state.events) == original_events_id, (
        "Events container identity should be preserved"
    )

    events_list = list(state.events)
    assert len(events_list) == 1, "State should have exactly one event after init_state"
    assert isinstance(events_list[0], SystemPromptEvent), (
        "The event should be a SystemPromptEvent"
    )

    assert len(events_collected) == 1, "on_event callback should be called once"
    assert events_collected[0].id == events_list[0].id, (
        "The event in state should have same ID as the one passed to callback"
    )


def test_init_state_adds_system_prompt_with_correct_content(tmp_path):
    """Test that init_state adds a SystemPromptEvent with the agent's system message."""
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    workspace = LocalWorkspace(working_dir=str(tmp_path))

    state = ConversationState.create(
        id=uuid.uuid4(),
        workspace=workspace,
        agent=agent,
    )

    def on_event(event):
        state.events.append(event)

    agent.init_state(state, on_event=on_event)

    events_list = list(state.events)
    assert isinstance(events_list[0], SystemPromptEvent)
    system_prompt_event: SystemPromptEvent = events_list[0]

    assert system_prompt_event.system_prompt.text == agent.system_message, (
        "SystemPromptEvent should contain the agent's system message"
    )
    assert system_prompt_event.source == "agent", (
        "SystemPromptEvent source should be 'agent'"
    )


def test_init_state_includes_tools_in_system_prompt(tmp_path):
    """Test that init_state includes the agent's tools in the SystemPromptEvent."""
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[], include_default_tools=["FinishTool", "ThinkTool"])
    workspace = LocalWorkspace(working_dir=str(tmp_path))

    state = ConversationState.create(
        id=uuid.uuid4(),
        workspace=workspace,
        agent=agent,
    )

    def on_event(event):
        state.events.append(event)

    agent.init_state(state, on_event=on_event)

    events_list = list(state.events)
    assert isinstance(events_list[0], SystemPromptEvent)
    system_prompt_event: SystemPromptEvent = events_list[0]

    tool_names = {tool.name for tool in system_prompt_event.tools}
    assert "finish" in tool_names, "SystemPromptEvent should include the finish tool"
    assert "think" in tool_names, "SystemPromptEvent should include the think tool"


def test_init_state_skips_if_system_prompt_exists(tmp_path):
    """Test that init_state does not add another SystemPromptEvent if one exists."""
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    workspace = LocalWorkspace(working_dir=str(tmp_path))

    state = ConversationState.create(
        id=uuid.uuid4(),
        workspace=workspace,
        agent=agent,
    )

    events_collected = []

    def on_event(event):
        events_collected.append(event)
        state.events.append(event)

    agent.init_state(state, on_event=on_event)

    assert len(events_collected) == 1, "First init_state should add one event"

    events_collected.clear()
    agent.init_state(state, on_event=on_event)

    assert len(events_collected) == 0, (
        "Second init_state should not add any events when SystemPromptEvent exists"
    )
    assert len(list(state.events)) == 1, (
        "State should still have only one SystemPromptEvent"
    )


def test_init_state_via_conversation_modifies_state_in_place(tmp_path):
    """Test that init_state via Conversation also modifies state in-place.

    This tests the integration path where init_state is called through the
    Conversation class, verifying the state is properly modified.
    """
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])

    conv = Conversation(
        agent=agent,
        visualizer=None,
        workspace=str(tmp_path),
    )
    conv._ensure_agent_ready()

    events_list = list(conv._state.events)
    assert len(events_list) == 1, (
        "State should have exactly one event after conversation initialization"
    )
    assert isinstance(events_list[0], SystemPromptEvent), (
        "The event should be a SystemPromptEvent"
    )
