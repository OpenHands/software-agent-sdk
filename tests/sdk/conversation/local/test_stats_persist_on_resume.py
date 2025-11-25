"""Test that conversation stats are properly persisted and restored on resume."""

import tempfile
import uuid
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import Agent, Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm_registry import RegistryEvent
from openhands.sdk.workspace import LocalWorkspace


def test_stats_preserved_on_resume():
    """Test that conversation stats including context_window are preserved on resume."""
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])

        conv_id = uuid.UUID("12345678-1234-5678-9abc-123456789010")
        persist_path_for_state = LocalConversation.get_persistence_dir(
            temp_dir, conv_id
        )

        # Create initial state
        state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path_for_state,
            agent=agent,
            id=conv_id,
        )

        # Register LLM and add metrics
        state.stats.register_llm(RegistryEvent(llm=llm))

        # Simulate LLM usage by adding token usage to metrics
        metrics = state.stats.get_metrics_for_usage("test-llm")
        metrics.add_token_usage(
            prompt_tokens=100,
            completion_tokens=50,
            cache_read_tokens=20,
            cache_write_tokens=10,
            context_window=8192,
            response_id="test-response-1",
        )
        metrics.add_cost(0.05)

        # Manually save since mutating stats doesn't trigger autosave
        state._save_base_state(state._fs)

        # Verify stats were recorded
        assert len(state.stats.usage_to_metrics) == 1
        assert "test-llm" in state.stats.usage_to_metrics
        initial_metrics = state.stats.usage_to_metrics["test-llm"]
        assert initial_metrics.accumulated_cost == 0.05
        assert initial_metrics.accumulated_token_usage is not None
        assert initial_metrics.accumulated_token_usage.context_window == 8192
        assert initial_metrics.accumulated_token_usage.prompt_tokens == 100

        # Verify base_state.json was saved
        assert Path(persist_path_for_state, "base_state.json").exists()

        # Now reload the state (simulating conversation resume)
        # This should preserve the stats
        resumed_state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path_for_state,
            agent=agent,
            id=conv_id,
        )

        # BUG: Stats should be preserved but they are reset to empty
        # After the fix, these assertions should pass
        assert len(resumed_state.stats.usage_to_metrics) == 1
        assert "test-llm" in resumed_state.stats.usage_to_metrics
        resumed_metrics = resumed_state.stats.usage_to_metrics["test-llm"]
        assert resumed_metrics.accumulated_cost == 0.05
        assert resumed_metrics.accumulated_token_usage is not None
        assert resumed_metrics.accumulated_token_usage.context_window == 8192
        assert resumed_metrics.accumulated_token_usage.prompt_tokens == 100


def test_full_state_event_includes_stats():
    """Test that full_state event includes stats with context_window."""
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])

        conv_id = uuid.UUID("12345678-1234-5678-9abc-123456789011")
        persist_path_for_state = LocalConversation.get_persistence_dir(
            temp_dir, conv_id
        )

        # Create state
        state = ConversationState.create(
            workspace=LocalWorkspace(working_dir="/tmp"),
            persistence_dir=persist_path_for_state,
            agent=agent,
            id=conv_id,
        )

        # Register LLM and add metrics
        state.stats.register_llm(RegistryEvent(llm=llm))
        metrics = state.stats.get_metrics_for_usage("test-llm")
        metrics.add_token_usage(
            prompt_tokens=200,
            completion_tokens=100,
            cache_read_tokens=30,
            cache_write_tokens=15,
            context_window=16384,
            response_id="test-response-2",
        )
        metrics.add_cost(0.10)

        # Create a full_state event
        event = ConversationStateUpdateEvent.from_conversation_state(state)

        # Verify event contains stats
        assert event.key == "full_state"
        assert "stats" in event.value
        assert "usage_to_metrics" in event.value["stats"]
        assert "test-llm" in event.value["stats"]["usage_to_metrics"]

        # Verify context_window is included and not 0
        llm_metrics = event.value["stats"]["usage_to_metrics"]["test-llm"]
        assert "accumulated_token_usage" in llm_metrics
        assert llm_metrics["accumulated_token_usage"]["context_window"] == 16384
        assert llm_metrics["accumulated_token_usage"]["prompt_tokens"] == 200
        assert llm_metrics["accumulated_token_usage"]["completion_tokens"] == 100
        assert llm_metrics["accumulated_cost"] == 0.10


def test_stats_in_conversation_via_full_state():
    """Test that stats are properly sent via full_state in a Conversation."""
    with tempfile.TemporaryDirectory() as temp_dir:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])

        conv_id = uuid.UUID("12345678-1234-5678-9abc-123456789012")

        # Create conversation
        conversation = Conversation(
            agent=agent,
            persistence_dir=temp_dir,
            workspace=LocalWorkspace(working_dir="/tmp"),
            conversation_id=conv_id,
        )

        # Register LLM and add metrics
        conversation._state.stats.register_llm(RegistryEvent(llm=llm))
        metrics = conversation._state.stats.get_metrics_for_usage("test-llm")
        metrics.add_token_usage(
            prompt_tokens=300,
            completion_tokens=150,
            cache_read_tokens=40,
            cache_write_tokens=20,
            context_window=32768,
            response_id="test-response-3",
        )

        # Create full_state event
        event = ConversationStateUpdateEvent.from_conversation_state(
            conversation._state
        )

        # Verify stats are in the event
        assert event.key == "full_state"
        assert "stats" in event.value
        llm_metrics = event.value["stats"]["usage_to_metrics"]["test-llm"]
        assert llm_metrics["accumulated_token_usage"]["context_window"] == 32768
