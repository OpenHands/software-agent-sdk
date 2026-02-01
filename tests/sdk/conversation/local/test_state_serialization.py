"""Test ConversationState serialization and persistence logic."""

import json
import tempfile
import uuid
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

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


class _DifferentAgentForVerifyTest(AgentBase):
    """A different agent class used to test Agent.verify() rejects class mismatches.

    This class is defined at module level (rather than inside a test function) to
    ensure it's importable by Pydantic during serialization/deserialization.
    Defining it inside a test function causes test pollution when running tests
    in parallel with pytest-xdist.
    """

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
        # Note: Do NOT register LLM stats here - this test verifies pure event
        # persistence. LLM stats registration happens during agent initialization
        # which is now lazy.

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
        # Note: Do NOT register LLM stats here - LLM registration happens during
        # agent initialization which is now lazy.

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

        # Corrupted JSON - will cause validation error when accessed
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

        # Load conversation - EventLog indexes files during init but doesn't
        # validate content until events are accessed
        conversation = Conversation(
            agent=agent,
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=temp_dir,
            conversation_id=conv_id,
        )

        # Accessing events triggers validation - corrupted JSON will fail
        with pytest.raises((ValidationError, json.JSONDecodeError)):
            # Iterate through all events to trigger loading
            for _ in conversation._state.events:
                pass


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

        # Agent initialization is lazy - trigger it to emit SystemPromptEvent
        conversation._ensure_agent_ready()

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
        # Note: With lazy initialization, system prompt not added until first use


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


def test_agent_pydantic_validation_on_creation():
    """Test that Pydantic validation happens when creating agents."""
    # Valid agent creation - Pydantic validates
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    assert agent.llm.model == "gpt-4o-mini"

    # Invalid LLM creation should fail Pydantic validation
    with pytest.raises(ValueError, match="model must be specified"):
        LLM(model="", api_key=SecretStr("test-key"), usage_id="test-llm")


def test_agent_verify_validates_tools_match():
    """Test that agent.verify() validates tools match between runtime and persisted."""
    from openhands.sdk.agent import AgentBase
    from openhands.sdk.tool import Tool

    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")

    # Create original agent with two tools
    original_agent = Agent(
        llm=llm, tools=[Tool(name="TerminalTool"), Tool(name="FileEditorTool")]
    )

    # Serialize and deserialize to simulate persistence
    serialized = original_agent.model_dump_json()
    persisted_agent = AgentBase.model_validate_json(serialized)

    # Runtime agent with same tools should succeed
    same_tools_agent = Agent(
        llm=llm, tools=[Tool(name="TerminalTool"), Tool(name="FileEditorTool")]
    )
    result = same_tools_agent.verify(persisted_agent)
    assert result is same_tools_agent

    # Runtime agent with different tools should fail
    different_tools_agent = Agent(llm=llm, tools=[Tool(name="TerminalTool")])
    with pytest.raises(ValueError, match="tools cannot be changed mid-conversation"):
        different_tools_agent.verify(persisted_agent)


def test_agent_verify_allows_different_llm():
    """Test that agent.verify() allows different LLM configuration."""
    from openhands.sdk.agent import AgentBase
    from openhands.sdk.tool import Tool

    tools = [Tool(name="TerminalTool")]

    # Create original agent
    llm1 = LLM(model="gpt-4o-mini", api_key=SecretStr("key1"), usage_id="llm1")
    original_agent = Agent(llm=llm1, tools=tools)

    # Serialize and deserialize
    serialized = original_agent.model_dump_json()
    persisted_agent = AgentBase.model_validate_json(serialized)

    # Runtime agent with different LLM should succeed (LLM can change freely)
    llm2 = LLM(model="gpt-4o", api_key=SecretStr("key2"), usage_id="llm2")
    different_llm_agent = Agent(llm=llm2, tools=tools)
    result = different_llm_agent.verify(persisted_agent)
    assert result is different_llm_agent
    assert result.llm.model == "gpt-4o"


def test_agent_verify_different_class_raises_error():
    """Test that agent.verify() raises error for different agent classes."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    original_agent = Agent(llm=llm, tools=[])
    different_agent = _DifferentAgentForVerifyTest()

    with pytest.raises(ValueError, match="Cannot load from persisted"):
        original_agent.verify(different_agent)


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
        # Test model_dump equality - stats should be preserved on resume
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

        # Send a message (this triggers lazy agent initialization)
        conversation.send_message(
            Message(role="user", content=[TextContent(text="test")])
        )

        # Store original state dump and ID before deleting
        # Exclude stats since LLM registration happens during agent init
        # and the second conversation will have its own stats after init
        original_state_dump = conversation._state.model_dump(
            mode="json", exclude={"agent", "stats"}
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
        # Test that the core state structure is preserved (excluding agent and stats)
        new_dump = new_conversation._state.model_dump(
            mode="json", exclude={"agent", "stats"}
        )

        assert new_dump == original_state_dump


def test_resume_uses_runtime_workspace_and_max_iterations():
    """Test that resume uses runtime-provided workspace and max_iterations."""
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])
        conv_id = uuid.UUID("12345678-1234-5678-9abc-123456789007")
        persist_path = LocalConversation.get_persistence_dir(temp_dir, conv_id)

        original_workspace = LocalWorkspace(working_dir="/original/path")
        state = ConversationState.create(
            workspace=original_workspace,
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
            max_iterations=100,
        )
        assert state.max_iterations == 100

        new_workspace = LocalWorkspace(working_dir="/new/path")
        resumed_state = ConversationState.create(
            workspace=new_workspace,
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
            max_iterations=200,
        )

        assert resumed_state.workspace.working_dir == "/new/path"
        assert resumed_state.max_iterations == 200


def test_resume_preserves_persisted_execution_status_and_stuck_detection():
    """Test that resume preserves execution_status and stuck_detection."""
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])
        conv_id = uuid.UUID("12345678-1234-5678-9abc-123456789008")
        persist_path = LocalConversation.get_persistence_dir(temp_dir, conv_id)

        state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
            stuck_detection=False,
        )
        state.execution_status = ConversationExecutionStatus.PAUSED

        resumed_state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
            stuck_detection=True,
        )

        assert resumed_state.execution_status == ConversationExecutionStatus.PAUSED
        assert resumed_state.stuck_detection is False


def test_resume_preserves_blocked_actions_and_messages():
    """Test that resume preserves blocked_actions and blocked_messages."""
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])
        conv_id = uuid.UUID("12345678-1234-5678-9abc-123456789009")
        persist_path = LocalConversation.get_persistence_dir(temp_dir, conv_id)

        state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
        )
        state.block_action("action-1", "dangerous action")
        state.block_message("msg-1", "inappropriate content")

        resumed_state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
        )

        assert resumed_state.blocked_actions["action-1"] == "dangerous action"
        assert resumed_state.blocked_messages["msg-1"] == "inappropriate content"


def test_conversation_state_stats_preserved_on_resume():
    """Regression: stats should not be reset when resuming a conversation."""
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])

        conv_id = uuid.UUID("12345678-1234-5678-9abc-123456789010")
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

        # Add token usage with context_window
        assert llm.metrics is not None
        llm.metrics.add_cost(0.05)
        llm.metrics.add_token_usage(
            prompt_tokens=100,
            completion_tokens=50,
            cache_read_tokens=10,
            cache_write_tokens=5,
            context_window=128000,
            response_id="test-response-1",
        )

        # Verify stats are set correctly before saving
        combined_metrics = state.stats.get_combined_metrics()
        assert combined_metrics.accumulated_cost == 0.05
        assert combined_metrics.accumulated_token_usage is not None
        assert combined_metrics.accumulated_token_usage.prompt_tokens == 100
        assert combined_metrics.accumulated_token_usage.context_window == 128000

        # Force save the state
        state._save_base_state(state._fs)

        # Verify the base_state.json contains the stats
        base_state_path = Path(persist_path_for_state) / "base_state.json"
        base_state_content = json.loads(base_state_path.read_text())
        assert "stats" in base_state_content
        assert "usage_to_metrics" in base_state_content["stats"]
        assert "test-llm" in base_state_content["stats"]["usage_to_metrics"]

        # Now resume the conversation with a new agent
        new_llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        new_agent = Agent(llm=new_llm, tools=[])

        resumed_state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path_for_state,
            agent=new_agent,
            id=conv_id,
        )

        # Verify stats are preserved after resume
        resumed_combined_metrics = resumed_state.stats.get_combined_metrics()
        assert resumed_combined_metrics.accumulated_cost == 0.05, (
            "Cost should be preserved after resume"
        )
        assert resumed_combined_metrics.accumulated_token_usage is not None
        assert resumed_combined_metrics.accumulated_token_usage.prompt_tokens == 100, (
            "Prompt tokens should be preserved after resume"
        )
        assert (
            resumed_combined_metrics.accumulated_token_usage.context_window == 128000
        ), "Context window should be preserved after resume"


def test_resume_with_conversation_id_mismatch_raises_error():
    """Test that resuming with mismatched conversation ID raises error."""
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])
        original_id = uuid.UUID("12345678-1234-5678-9abc-12345678900b")
        different_id = uuid.UUID("12345678-1234-5678-9abc-12345678900c")
        persist_path = LocalConversation.get_persistence_dir(temp_dir, original_id)

        ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path,
            agent=agent,
            id=original_id,
        )

        with pytest.raises(ValueError, match="Conversation ID mismatch"):
            ConversationState.create(
                workspace=LocalWorkspace(working_dir="/tmp"),
                persistence_dir=persist_path,
                agent=agent,
                id=different_id,
            )


def test_conversation_state_secrets_serialization_deserialization():
    """Test that secrets are properly serialized and deserialized.

    This is a regression test for issue 1505 where conversations with secrets
    would fail to restore because secrets are serialized as '**********'
    (redacted) but StaticSecret.value was a required field that couldn't
    accept None after validation converted '**********' to None.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])
        conv_id = uuid.UUID("12345678-1234-5678-9abc-123456789099")
        persist_path = LocalConversation.get_persistence_dir(temp_dir, conv_id)

        # Create conversation state with secrets
        state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
        )

        # Add secrets to the secret registry
        state.secret_registry.update_secrets(
            {
                "API_KEY": "test-api-key",
                "DATABASE_URL": "postgresql://localhost/test",
            }
        )

        # Verify secrets are set before save
        env_vars = state.secret_registry.get_secrets_as_env_vars("echo $API_KEY")
        assert env_vars == {"API_KEY": "test-api-key"}

        # Force save the state (triggers serialization)
        state._save_base_state(state._fs)

        # Verify the serialized state has redacted secrets
        base_state_path = Path(persist_path) / "base_state.json"
        base_state_content = json.loads(base_state_path.read_text())
        assert "secret_registry" in base_state_content
        api_key_source = base_state_content["secret_registry"]["secret_sources"][
            "API_KEY"
        ]
        # Value should be redacted to '**********' in serialization
        assert api_key_source["value"] == "**********"

        # Now simulate restoring the conversation state from persisted data
        # This was failing before the fix with:
        # "pydantic_core._pydantic_core.ValidationError: Field required
        # [type=missing, ... for StaticSecret.value"
        resumed_state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
        )

        # The state should load successfully - this was the bug fix
        assert resumed_state.id == conv_id

        # The secrets should be None after restore (since they were redacted)
        # but the StaticSecret objects should exist
        assert "API_KEY" in resumed_state.secret_registry.secret_sources
        assert "DATABASE_URL" in resumed_state.secret_registry.secret_sources

        # The values should be None after deserialization of redacted secrets
        api_key_source_restored = resumed_state.secret_registry.secret_sources[
            "API_KEY"
        ]
        assert api_key_source_restored.get_value() is None

        # Getting env vars should return empty since values are None
        env_vars = resumed_state.secret_registry.get_secrets_as_env_vars(
            "echo $API_KEY"
        )
        assert env_vars == {}  # No value available


def test_conversation_state_secrets_with_cipher():
    """Test that secrets are preserved when using a cipher.

    When a cipher is provided to ConversationState.create(), secrets should
    be encrypted during serialization and decrypted during deserialization,
    preserving the actual secret values across save/restore cycles.
    """
    from openhands.sdk.utils.cipher import Cipher

    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])
        conv_id = uuid.UUID("12345678-1234-5678-9abc-1234567890aa")
        persist_path = LocalConversation.get_persistence_dir(temp_dir, conv_id)

        # Create a cipher for encryption
        cipher = Cipher(secret_key="my-secret-encryption-key")

        # Create conversation state with secrets AND cipher
        state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
            cipher=cipher,
        )

        # Add secrets to the secret registry
        state.secret_registry.update_secrets(
            {
                "API_KEY": "test-api-key",
                "DATABASE_URL": "postgresql://localhost/test",
            }
        )

        # Verify secrets are set before save
        env_vars = state.secret_registry.get_secrets_as_env_vars("echo $API_KEY")
        assert env_vars == {"API_KEY": "test-api-key"}

        # Force save the state (triggers serialization with encryption)
        state._save_base_state(state._fs)

        # Verify the serialized state has encrypted (not redacted) secrets
        base_state_path = Path(persist_path) / "base_state.json"
        base_state_content = json.loads(base_state_path.read_text())
        assert "secret_registry" in base_state_content
        api_key_source = base_state_content["secret_registry"]["secret_sources"][
            "API_KEY"
        ]
        # Value should be encrypted (not '**********')
        assert api_key_source["value"] != "**********"
        assert api_key_source["value"] != "test-api-key"  # Not plaintext
        assert len(api_key_source["value"]) > 20  # Encrypted value is longer

        # Now restore the conversation state with the same cipher
        resumed_state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
            cipher=cipher,
        )

        # The state should load successfully
        assert resumed_state.id == conv_id

        # The secrets should be PRESERVED after restore
        assert "API_KEY" in resumed_state.secret_registry.secret_sources
        assert "DATABASE_URL" in resumed_state.secret_registry.secret_sources

        # The values should be decrypted and accessible
        api_key_source_restored = resumed_state.secret_registry.secret_sources[
            "API_KEY"
        ]
        assert api_key_source_restored.get_value() == "test-api-key"

        # Getting env vars should return the actual values
        env_vars = resumed_state.secret_registry.get_secrets_as_env_vars(
            "echo $API_KEY"
        )
        assert env_vars == {"API_KEY": "test-api-key"}

        db_env_vars = resumed_state.secret_registry.get_secrets_as_env_vars(
            "echo $DATABASE_URL"
        )
        assert db_env_vars == {"DATABASE_URL": "postgresql://localhost/test"}


def test_conversation_state_save_with_cipher_load_without():
    """Test loading state saved with cipher but without providing cipher.

    When state is saved with a cipher (secrets encrypted) but loaded without
    a cipher, the encrypted values should remain as-is (unusable) but the
    conversation should still load successfully.
    """
    from openhands.sdk.utils.cipher import Cipher

    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])
        conv_id = uuid.UUID("12345678-1234-5678-9abc-1234567890bb")
        persist_path = LocalConversation.get_persistence_dir(temp_dir, conv_id)

        # Create a cipher for encryption
        cipher = Cipher(secret_key="my-secret-encryption-key")

        # Create conversation state with secrets AND cipher
        state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
            cipher=cipher,
        )

        # Add secrets to the secret registry
        state.secret_registry.update_secrets({"API_KEY": "test-api-key"})

        # Force save the state (triggers serialization with encryption)
        state._save_base_state(state._fs)

        # Now restore WITHOUT a cipher - should load but secrets are unusable
        resumed_state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
            cipher=None,  # No cipher provided
        )

        # The state should load successfully
        assert resumed_state.id == conv_id

        # The secret source should exist but value is the encrypted string
        # (not decrypted, so not usable as the original value)
        assert "API_KEY" in resumed_state.secret_registry.secret_sources
        api_key_value = resumed_state.secret_registry.secret_sources[
            "API_KEY"
        ].get_value()
        # Value should be the encrypted string, not the original
        assert api_key_value != "test-api-key"
        assert api_key_value is not None  # It's the encrypted value


def test_conversation_state_save_without_cipher_load_with():
    """Test loading state saved without cipher but with cipher provided.

    When state is saved without a cipher (secrets redacted) but loaded with
    a cipher, the redacted secrets should deserialize to None values.
    """
    from openhands.sdk.utils.cipher import Cipher

    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])
        conv_id = uuid.UUID("12345678-1234-5678-9abc-1234567890cc")
        persist_path = LocalConversation.get_persistence_dir(temp_dir, conv_id)

        # Create conversation state with secrets but NO cipher
        state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
            cipher=None,  # No cipher - secrets will be redacted
        )

        # Add secrets to the secret registry
        state.secret_registry.update_secrets({"API_KEY": "test-api-key"})

        # Force save the state (triggers serialization with redaction)
        state._save_base_state(state._fs)

        # Now restore WITH a cipher - should load but secrets are already lost
        cipher = Cipher(secret_key="my-secret-encryption-key")
        resumed_state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
            cipher=cipher,
        )

        # The state should load successfully
        assert resumed_state.id == conv_id

        # The secret source should exist but value is None (was redacted)
        assert "API_KEY" in resumed_state.secret_registry.secret_sources
        api_key_value = resumed_state.secret_registry.secret_sources[
            "API_KEY"
        ].get_value()
        assert api_key_value is None


def test_conversation_state_cipher_mismatch():
    """Test loading state with a different cipher than used for saving.

    When state is saved with cipher A but loaded with cipher B, decryption
    fails gracefully - the conversation loads but secrets are set to None
    (with a warning logged).
    """
    from openhands.sdk.utils.cipher import Cipher

    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])
        conv_id = uuid.UUID("12345678-1234-5678-9abc-1234567890dd")
        persist_path = LocalConversation.get_persistence_dir(temp_dir, conv_id)

        # Create cipher A for encryption
        cipher_a = Cipher(secret_key="cipher-key-a")

        # Create conversation state with secrets AND cipher A
        state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
            cipher=cipher_a,
        )

        # Add secrets to the secret registry
        state.secret_registry.update_secrets({"API_KEY": "test-api-key"})

        # Force save the state (triggers serialization with encryption using cipher A)
        state._save_base_state(state._fs)

        # Now try to restore with cipher B - decryption fails gracefully
        cipher_b = Cipher(secret_key="cipher-key-b")

        # Conversation loads but secrets are lost (set to None with warning)
        resumed_state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path,
            agent=agent,
            id=conv_id,
            cipher=cipher_b,
        )

        # The state should load successfully
        assert resumed_state.id == conv_id

        # The secret source should exist but value is None (decryption failed)
        assert "API_KEY" in resumed_state.secret_registry.secret_sources
        api_key_value = resumed_state.secret_registry.secret_sources[
            "API_KEY"
        ].get_value()
        assert api_key_value is None


def test_agent_verify_fails_when_explicit_tools_differ():
    """Test that verify() fails when explicit tools differ.

    Tools cannot be changed mid-conversation. This test verifies that
    changing explicit tools fails verification.
    """
    from openhands.sdk.agent import AgentBase
    from openhands.sdk.tool import Tool

    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")

    # Create persisted agent with TerminalTool
    persisted_agent_obj = Agent(
        llm=llm,
        tools=[Tool(name="TerminalTool")],
        include_default_tools=["FinishTool"],
    )

    # Serialize and deserialize to simulate loading from persistence
    serialized = persisted_agent_obj.model_dump_json()
    persisted_agent = AgentBase.model_validate_json(serialized)

    # Create a runtime agent with DIFFERENT explicit tools (FileEditorTool instead of
    # TerminalTool) - this should FAIL because tools must match exactly
    runtime_agent = Agent(
        llm=llm,
        tools=[Tool(name="FileEditorTool")],  # Different from persisted!
        include_default_tools=["FinishTool"],
    )

    # Should fail because explicit tools don't match (TerminalTool vs FileEditorTool)
    with pytest.raises(ValueError, match="tools cannot be changed mid-conversation"):
        runtime_agent.verify(persisted_agent)


def test_agent_verify_fails_when_builtin_tools_differ():
    """Test that verify() fails when builtin tools differ.

    Tools cannot be changed mid-conversation. This test verifies that
    changing builtin tools (include_default_tools) fails verification,
    even when explicit tools match.
    """
    from openhands.sdk.agent import AgentBase
    from openhands.sdk.tool import Tool

    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")

    # Persisted agent has FinishTool as builtin
    persisted_agent_obj = Agent(
        llm=llm,
        tools=[Tool(name="TerminalTool")],
        include_default_tools=["FinishTool"],
    )

    serialized = persisted_agent_obj.model_dump_json()
    persisted_agent = AgentBase.model_validate_json(serialized)

    # Runtime agent has ThinkTool instead of FinishTool (same explicit tools)
    runtime_agent = Agent(
        llm=llm,
        tools=[Tool(name="TerminalTool")],  # Same explicit tools
        include_default_tools=["ThinkTool"],  # Different builtin!
    )

    # Should fail because builtin tools don't match (FinishTool vs ThinkTool)
    with pytest.raises(ValueError, match="tools cannot be changed mid-conversation"):
        runtime_agent.verify(persisted_agent)


def test_agent_verify_fails_when_builtin_tool_removed():
    """Test that verify fails when a builtin tool is removed."""
    from openhands.sdk.agent import AgentBase
    from openhands.sdk.tool import Tool

    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")

    persisted_agent_obj = Agent(
        llm=llm,
        tools=[Tool(name="TerminalTool")],
        include_default_tools=["FinishTool", "ThinkTool"],  # Has both
    )

    serialized = persisted_agent_obj.model_dump_json()
    persisted_agent = AgentBase.model_validate_json(serialized)

    # Runtime agent removes ThinkTool
    runtime_agent = Agent(
        llm=llm,
        tools=[Tool(name="TerminalTool")],
        include_default_tools=["FinishTool"],  # Missing ThinkTool!
    )

    # Should fail because builtin tools don't match
    with pytest.raises(ValueError, match="tools cannot be changed mid-conversation"):
        runtime_agent.verify(persisted_agent)
