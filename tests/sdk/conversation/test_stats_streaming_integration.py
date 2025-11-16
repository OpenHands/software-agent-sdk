"""Integration test for stats streaming during conversation execution."""

import uuid
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, RegistryEvent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event import Event
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.io import InMemoryFileStore
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def state():
    """Create a ConversationState for testing."""
    llm = LLM(model="gpt-4", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm)
    workspace = LocalWorkspace(working_dir="/tmp/test")

    state = ConversationState(
        id=uuid.uuid4(),
        workspace=workspace,
        persistence_dir="/tmp/test/.state",
        agent=agent,
    )

    # Set up filestore and enable autosave so callbacks are triggered
    state._fs = InMemoryFileStore()
    state._autosave_enabled = True

    return state


def test_metrics_updates_trigger_state_change_events(state):
    """
    Test that when LLM metrics are updated during execution,
    the state change callback is triggered with stats updates.

    This is the key integration test for issue #1087.
    """
    state_change_events = []

    def state_callback(event: Event):
        if isinstance(event, ConversationStateUpdateEvent):
            state_change_events.append(event)

    # Set up the state change callback
    state.set_on_state_change(state_callback)

    # Register an LLM (simulating agent initialization)
    with patch("openhands.sdk.llm.llm.litellm_completion"):
        llm = LLM(
            usage_id="test-service",
            model="gpt-4o",
            api_key=SecretStr("test_key"),
            num_retries=2,
            retry_min_wait=1,
            retry_max_wait=2,
        )
        event = RegistryEvent(llm=llm)
        state.stats.register_llm(event)

    # Clear the initial registration event
    state_change_events.clear()

    # Simulate LLM usage during conversation execution
    # This is what happens when the agent makes LLM calls
    llm.metrics.add_cost(0.05)
    llm.metrics.add_token_usage(
        prompt_tokens=500,
        completion_tokens=200,
        cache_read_tokens=0,
        cache_write_tokens=0,
        context_window=8000,
        response_id="resp1",
    )

    # Verify that state change events were generated
    assert len(state_change_events) == 2  # One for cost, one for token usage

    # Verify the events are stats updates
    for event in state_change_events:
        assert event.key == "stats"
        assert isinstance(event.value, dict)
        assert "usage_to_metrics" in event.value

    # Verify stats contain the updated costs
    final_stats = state_change_events[-1].value
    assert "test-service" in final_stats["usage_to_metrics"]
    service_metrics = final_stats["usage_to_metrics"]["test-service"]
    assert service_metrics["accumulated_cost"] == 0.05
    assert service_metrics["accumulated_token_usage"]["prompt_tokens"] == 500
    assert service_metrics["accumulated_token_usage"]["completion_tokens"] == 200


def test_multiple_llms_metrics_updates_all_trigger_events(state):
    """Test that metrics updates from multiple LLMs all trigger state events."""
    state_change_events = []

    def state_callback(event: Event):
        if isinstance(event, ConversationStateUpdateEvent):
            state_change_events.append(event)

    state.set_on_state_change(state_callback)

    # Register two LLMs
    with patch("openhands.sdk.llm.llm.litellm_completion"):
        llm1 = LLM(
            usage_id="service-1",
            model="gpt-4o",
            api_key=SecretStr("test_key"),
            num_retries=2,
            retry_min_wait=1,
            retry_max_wait=2,
        )
        llm2 = LLM(
            usage_id="service-2",
            model="claude-3",
            api_key=SecretStr("test_key"),
            num_retries=2,
            retry_min_wait=1,
            retry_max_wait=2,
        )

        state.stats.register_llm(RegistryEvent(llm=llm1))
        state.stats.register_llm(RegistryEvent(llm=llm2))

    state_change_events.clear()

    # Simulate updates from both LLMs
    llm1.metrics.add_cost(0.05)
    llm2.metrics.add_cost(0.03)

    # Both updates should trigger events
    assert len(state_change_events) == 2

    # Both should be stats events
    for event in state_change_events:
        assert event.key == "stats"


def test_callback_removal_stops_stats_streaming(state):
    """Test that removing the callback stops stats streaming."""
    state_change_events = []

    def state_callback(event: Event):
        if isinstance(event, ConversationStateUpdateEvent):
            state_change_events.append(event)

    state.set_on_state_change(state_callback)

    with patch("openhands.sdk.llm.llm.litellm_completion"):
        llm = LLM(
            usage_id="test-service",
            model="gpt-4o",
            api_key=SecretStr("test_key"),
            num_retries=2,
            retry_min_wait=1,
            retry_max_wait=2,
        )
        state.stats.register_llm(RegistryEvent(llm=llm))

    state_change_events.clear()

    # Remove the callback
    state.set_on_state_change(None)

    # Update metrics
    llm.metrics.add_cost(0.05)

    # No events should be generated
    assert len(state_change_events) == 0
