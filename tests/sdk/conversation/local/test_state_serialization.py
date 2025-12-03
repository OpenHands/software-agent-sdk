"""Test ConversationState serialization and persistence logic."""

import json
import tempfile
import uuid
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk import Agent, Conversation
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.conversation.types import (
    ConversationCallbackType,
    ConversationTokenCallbackType,
)
from openhands.sdk.event.llm_convertible import MessageEvent, SystemPromptEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.llm_registry import RegistryEvent
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.sdk.workspace import LocalWorkspace


def test_conversation_state_basic_serialization():
    """Test basic ConversationState serialization and deserialization."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    state = ConversationState.create(
        agent=agent,
        id=uuid.UUID("12345678-1234-5678-9abc-123456789001"),
        workspace=LocalWorkspace(working_dir="/tmp"),
    )

    # Add some events
    event1 = SystemPromptEvent(
        source="agent", system_prompt=TextContent(text="system"), tools=[]
    )
    event2 = MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="hello")]),
    )
    state.events.append(event1)
    state.events.append(event2)

    # Test serialization - note that events are not included in base state
    serialized = state.model_dump_json(exclude_none=True)
    assert isinstance(serialized, str)

    # Test deserialization - events won't be included in base state
    deserialized = ConversationState.model_validate_json(serialized)
    assert deserialized.id == state.id

    # Events are stored separately, so we need to check the actual events
    # through the EventLog, not through serialization
    assert len(state.events) >= 2  # May have additional events from Agent.init_state

    # Find our test events
    our_events = [
        e
        for e in state.events
        if isinstance(e, (SystemPromptEvent, MessageEvent))
        and e.source in ["agent", "user"]
    ]
    assert len(our_events) >= 2
    assert deserialized.agent.llm.model == state.agent.llm.model
    assert deserialized.agent.__class__ == state.agent.__class__

    # Verify agent properties
    assert deserialized.agent.llm.model == agent.llm.model
    assert deserialized.agent.__class__ == agent.__class__


def test_conversation_state_persistence_save_load():
    """Test ConversationState persistence with FileStore."""
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])

        conv_id = uuid.UUID("12345678-1234-5678-9abc-123456789002")
        persist_path_for_state = LocalConversation.get_persistence_dir(
            temp_dir, conv_id
        )
        state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path_for_state,
            agent=agent,
            id=conv_id,
        )

        # Add events
        event1 = SystemPromptEvent(
            source="agent", system_prompt=TextContent(text="system"), tools=[]
        )
        event2 = MessageEvent(
            source="user",
            llm_message=Message(role="user", content=[TextContent(text="hello")]),
        )
        state.events.append(event1)
        state.events.append(event2)
        state.stats.register_llm(RegistryEvent(llm=llm))

        # State auto-saves when events are added
        # Verify files were created
        assert Path(persist_path_for_state, "base_state.json").exists()

        # Events are stored with new naming pattern
        event_files = list(Path(persist_path_for_state, "events").glob("*.json"))
        assert len(event_files) == 2

        # Load state using Conversation (which handles loading)
        conversation = Conversation(
            agent=agent,
            persistence_dir=temp_dir,
            workspace=LocalWorkspace(working_dir="/tmp"),
            conversation_id=conv_id,
        )
        assert isinstance(conversation, LocalConversation)
        loaded_state = conversation._state
        assert conversation.state.persistence_dir == persist_path_for_state

        # Verify loaded state matches original
        assert loaded_state.id == state.id
        assert len(loaded_state.events) == 2
        assert isinstance(loaded_state.events[0], SystemPromptEvent)
        assert isinstance(loaded_state.events[1], MessageEvent)
        assert loaded_state.agent.llm.model == agent.llm.model
        assert loaded_state.agent.__class__ == agent.__class__
        # Test model_dump equality
        assert loaded_state.model_dump(mode="json") == state.model_dump(mode="json")

        # Also verify key fields are preserved
        assert loaded_state.id == state.id
        assert len(loaded_state.events) == len(state.events)


def test_conversation_state_incremental_save():
    """Test that ConversationState saves events incrementally."""
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])

        conv_id = uuid.UUID("12345678-1234-5678-9abc-123456789003")
        persist_path_for_state = LocalConversation.get_persistence_dir(
            temp_dir, conv_id
        )
        state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path_for_state,
            agent=agent,
            id=uuid.UUID("12345678-1234-5678-9abc-123456789003"),
        )

        # Add first event - auto-saves
        event1 = SystemPromptEvent(
            source="agent", system_prompt=TextContent(text="system"), tools=[]
        )
        state.events.append(event1)
        state.stats.register_llm(RegistryEvent(llm=llm))

        # Verify event files exist (may have additional events from Agent.init_state)
        event_files = list(Path(persist_path_for_state, "events").glob("*.json"))
        assert len(event_files) == 1

        # Add second event - auto-saves
        event2 = MessageEvent(
            source="user",
            llm_message=Message(role="user", content=[TextContent(text="hello")]),
        )
        state.events.append(event2)

        # Verify additional event file was created
        event_files = list(Path(persist_path_for_state, "events").glob("*.json"))
        assert len(event_files) == 2

        # Load using Conversation and verify events are present
        conversation = Conversation(
            agent=agent,
            persistence_dir=temp_dir,
            workspace=LocalWorkspace(working_dir="/tmp"),
            conversation_id=conv_id,
        )
        assert isinstance(conversation, LocalConversation)
        assert conversation.state.persistence_dir == persist_path_for_state
        loaded_state = conversation._state
        assert len(loaded_state.events) == 2
        # Test model_dump equality
        assert loaded_state.model_dump(mode="json") == state.model_dump(mode="json")


def test_conversation_state_event_file_scanning():
    """Test event file scanning and sorting logic through EventLog."""
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])

        conv_id = uuid.UUID("12345678-1234-5678-9abc-123456789004")
        persist_path_for_state = LocalConversation.get_persistence_dir(
            temp_dir, conv_id
        )

        # Create event files with valid format (new pattern)
        events_dir = Path(persist_path_for_state, "events")
        events_dir.mkdir(parents=True, exist_ok=True)

        # Create files with different indices using valid event format
        event1 = SystemPromptEvent(
            id="abcdef01",
            source="agent",
            system_prompt=TextContent(text="system1"),
            tools=[],
        )
        (events_dir / "event-00000-abcdef01.json").write_text(
            event1.model_dump_json(exclude_none=True)
        )

        event2 = SystemPromptEvent(
            id="abcdef02",
            source="agent",
            system_prompt=TextContent(text="system2"),
            tools=[],
        )
        (events_dir / "event-00001-abcdef02.json").write_text(
            event2.model_dump_json(exclude_none=True)
        )

        # Invalid file should be ignored
        (events_dir / "invalid-file.json").write_text('{"type": "test"}')

        # Load state - EventLog should handle scanning
        conversation = Conversation(
            agent=agent,
            persistence_dir=temp_dir,
            workspace=LocalWorkspace(working_dir="/tmp"),
            conversation_id=conv_id,
        )

        # Should load valid events in order
        assert (
            len(conversation._state.events) == 2
        )  # May have additional events from Agent.init_state

        # Find our test events
        our_events = [
            e
            for e in conversation._state.events
            if isinstance(e, SystemPromptEvent) and e.id in ["abcdef01", "abcdef02"]
        ]
        assert len(our_events) == 2


def test_conversation_state_corrupted_event_handling():
    """Test handling of corrupted event files during replay."""
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])

        # Create event files with some corrupted
        conv_id = uuid.uuid4()
        persist_path_for_state = LocalConversation.get_persistence_dir(
            temp_dir, conv_id
        )
        events_dir = Path(persist_path_for_state, "events")
        events_dir.mkdir(parents=True, exist_ok=True)

        # Valid event with proper format
        valid_event = SystemPromptEvent(
            id="abcdef01",
            source="agent",
            system_prompt=TextContent(text="system"),
            tools=[],
        )
        (events_dir / "event-00000-abcdef01.json").write_text(
            valid_event.model_dump_json(exclude_none=True)
        )

        # Corrupted JSON - will be ignored by EventLog
        (events_dir / "event-00001-abcdef02.json").write_text('{"invalid": json}')

        # Empty file - will be ignored by EventLog
        (events_dir / "event-00002-abcdef03.json").write_text("")

        # Valid event with proper format
        valid_event2 = MessageEvent(
            id="abcdef04",
            source="user",
            llm_message=Message(role="user", content=[TextContent(text="hello")]),
        )
        (events_dir / "event-00003-abcdef04.json").write_text(
            valid_event2.model_dump_json(exclude_none=True)
        )

        # Load conversation - EventLog will fail on corrupted files
        with pytest.raises(json.JSONDecodeError):
            Conversation(
                agent=agent,
                workspace=LocalWorkspace(working_dir="/tmp"),
                persistence_dir=temp_dir,
                conversation_id=conv_id,
            )


def test_conversation_state_empty_filestore():
    """Test ConversationState behavior with empty persistence directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])

        # Create conversation with empty persistence directory
        conversation = Conversation(
            agent=agent,
            persistence_dir=temp_dir,
            workspace=LocalWorkspace(working_dir="/tmp"),
            visualizer=None,
        )

        # Should create new state
        assert conversation._state.id is not None
        assert len(conversation._state.events) == 1  # System prompt event
        assert isinstance(conversation._state.events[0], SystemPromptEvent)


def test_conversation_state_missing_base_state():
    """Test error handling when base_state.json is missing but events exist."""
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])

        # Create events directory with files but no base_state.json
        events_dir = Path(temp_dir, "events")
        events_dir.mkdir()
        event = SystemPromptEvent(
            id="abcdef01",
            source="agent",
            system_prompt=TextContent(text="system"),
            tools=[],
        )
        (events_dir / "event-00000-abcdef01.json").write_text(
            event.model_dump_json(exclude_none=True)
        )

        # Current implementation creates new conversation and ignores orphaned
        # event files
        conversation = Conversation(
            agent=agent,
            persistence_dir=temp_dir,
            workspace=LocalWorkspace(working_dir="/tmp"),
        )

        # Should create new state, not load the orphaned event file
        assert conversation._state.id is not None
        assert (
            len(conversation._state.events) >= 1
        )  # At least system prompt from Agent.init_state


def test_conversation_state_exclude_from_base_state():
    """Test that events are excluded from base state serialization."""
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])
        state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=temp_dir,
            agent=agent,
            id=uuid.UUID("12345678-1234-5678-9abc-123456789004"),
        )

        # Add events
        event = SystemPromptEvent(
            source="agent", system_prompt=TextContent(text="system"), tools=[]
        )
        state.events.append(event)

        # State auto-saves, read base state file directly
        base_state_path = Path(temp_dir) / "base_state.json"
        base_state_content = base_state_path.read_text()
        base_state_data = json.loads(base_state_content)

        # Events should not be in base state
        assert "events" not in base_state_data
        assert "agent" in base_state_data
        assert "id" in base_state_data


def test_conversation_state_thread_safety():
    """Test ConversationState thread safety with lock/unlock."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    state = ConversationState.create(
        workspace=LocalWorkspace(working_dir="/tmp"),
        agent=agent,
        id=uuid.UUID("12345678-1234-5678-9abc-123456789005"),
    )

    # Test context manager
    with state:
        assert state.owned()
        # Should be owned by current thread when locked

    # Test manual acquire/release
    state.acquire()
    try:
        assert state.owned()
    finally:
        state.release()

    # Test that state is not owned when not locked
    assert not state.owned()


def test_agent_resolve_diff_different_class_raises_error():
    """Test that resolve_diff_from_deserialized raises error for different agent classes."""  # noqa: E501

    class DifferentAgent(AgentBase):
        def __init__(self):
            llm = LLM(
                model="gpt-4o-mini",
                api_key=SecretStr("test-key"),
                usage_id="test-llm",
            )
            super().__init__(llm=llm, tools=[])

        def init_state(self, state, on_event):
            pass

        def step(
            self,
            conversation,
            on_event: ConversationCallbackType,
            on_token: ConversationTokenCallbackType | None = None,
        ):
            pass

    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    original_agent = Agent(llm=llm, tools=[])
    different_agent = DifferentAgent()

    with pytest.raises(ValueError, match="Cannot resolve from deserialized"):
        original_agent.resolve_diff_from_deserialized(different_agent)


def test_conversation_state_flags_persistence():
    """Test that conversation state flags are properly persisted."""
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])
        conv_id = uuid.UUID("12345678-1234-5678-9abc-123456789006")
        persist_path_for_state = LocalConversation.get_persistence_dir(
            temp_dir, conv_id
        )
        state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path_for_state,
            agent=agent,
            id=conv_id,
        )

        state.stats.register_llm(RegistryEvent(llm=llm))

        # Set various flags
        state.execution_status = ConversationExecutionStatus.FINISHED
        state.confirmation_policy = AlwaysConfirm()
        state.activated_knowledge_skills = ["agent1", "agent2"]

        # Create a new ConversationState that loads from the same persistence directory
        loaded_state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path_for_state,
            agent=agent,
            id=conv_id,
        )

        # Verify key fields are preserved
        assert loaded_state.id == state.id
        assert loaded_state.agent.llm.model == state.agent.llm.model
        # Verify flags are preserved
        assert loaded_state.execution_status == ConversationExecutionStatus.FINISHED
        assert loaded_state.confirmation_policy == AlwaysConfirm()
        assert loaded_state.activated_knowledge_skills == ["agent1", "agent2"]
        # Test model_dump equality
        assert loaded_state.model_dump(mode="json") != state.model_dump(mode="json")
        loaded_state.stats.register_llm(RegistryEvent(llm=llm))
        assert loaded_state.model_dump(mode="json") == state.model_dump(mode="json")


def test_conversation_with_agent_different_llm_config():
    """Test conversation with agent having different LLM configuration."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create conversation with original LLM config
        original_llm = LLM(
            model="gpt-4o-mini",
            api_key=SecretStr("original-key"),
            usage_id="test-llm",
        )
        original_agent = Agent(llm=original_llm, tools=[])
        conversation = Conversation(
            agent=original_agent,
            persistence_dir=temp_dir,
            workspace=LocalWorkspace(working_dir="/tmp"),
            visualizer=None,
        )

        # Send a message
        conversation.send_message(
            Message(role="user", content=[TextContent(text="test")])
        )

        # Store original state dump and ID before deleting
        original_state_dump = conversation._state.model_dump(
            mode="json", exclude={"agent"}
        )
        conversation_id = conversation._state.id

        del conversation

        # Try with different LLM config (different API key should be resolved)
        new_llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("new-key"), usage_id="test-llm"
        )
        new_agent = Agent(llm=new_llm, tools=[])

        # This should succeed because API key differences are resolved
        new_conversation = Conversation(
            agent=new_agent,
            persistence_dir=temp_dir,
            workspace=LocalWorkspace(working_dir="/tmp"),
            conversation_id=conversation_id,  # Use same ID
            visualizer=None,
        )

        assert new_conversation._state.agent.llm.api_key is not None
        assert isinstance(new_conversation._state.agent.llm.api_key, SecretStr)
        assert new_conversation._state.agent.llm.api_key.get_secret_value() == "new-key"
        # Test that the core state structure is preserved (excluding agent differences)
        new_dump = new_conversation._state.model_dump(mode="json", exclude={"agent"})

        assert new_dump == original_state_dump
