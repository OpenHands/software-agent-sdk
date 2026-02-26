"""Core high-level tests for Conversation class focusing on essential
functionality."""

import os
import tempfile
import uuid

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import LLM, Message, TextContent


def create_test_agent() -> Agent:
    """Create a test agent."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    return Agent(llm=llm, tools=[])


def create_test_event(event_id: str, content: str = "Test content") -> MessageEvent:
    """Create a test MessageEvent with specific ID."""
    event = MessageEvent(
        id=event_id,
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )
    return event


def test_conversation_basic_creation():
    """Test basic conversation creation and properties."""
    agent = create_test_agent()

    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(agent=agent, persistence_dir=tmpdir, workspace=tmpdir)

        # Basic properties should be set
        assert conv.id is not None
        assert isinstance(conv.id, uuid.UUID)  # UUID type
        assert conv.state is not None
        assert conv._state.agent == agent


def test_conversation_event_log_functionality():
    """Test EventLog integration with Conversation."""
    agent = create_test_agent()

    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(agent=agent, persistence_dir=tmpdir, workspace=tmpdir)

        # Add events directly to test EventLog functionality
        events = [
            create_test_event("event-1", "First message"),
            create_test_event("event-2", "Second message"),
            create_test_event("event-3", "Third message"),
        ]

        for event in events:
            conv.state.events.append(event)

        # Test basic EventLog functionality
        total_events = len(conv.state.events)
        assert total_events >= 3  # May have additional events from Agent.init_state

        # Find our test events
        our_events = [e for e in conv.state.events if e.id.startswith("event-")]
        assert len(our_events) == 3
        assert our_events[0].id == "event-1"
        assert our_events[1].id == "event-2"
        assert our_events[2].id == "event-3"

        # Test iteration
        event_ids = [e.id for e in our_events]
        assert event_ids == ["event-1", "event-2", "event-3"]


def test_conversation_state_persistence():
    """Test conversation state persistence to file store."""
    agent = create_test_agent()

    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(agent=agent, persistence_dir=tmpdir, workspace=tmpdir)

        # Add an event
        event = create_test_event("persist-test", "Persistence test")
        conv.state.events.append(event)

        # State should auto-save when events are added
        # Check that files were created
        import os

        # The persistence directory is actually a subdirectory
        persistence_files = os.listdir(conv.state.persistence_dir)
        assert len(persistence_files) > 0

        # Should have base state file
        base_state_exists = any("base_state.json" in f for f in persistence_files)
        assert base_state_exists

        # Should have events directory
        if conv.state.persistence_dir:
            events_dir = os.path.join(conv.state.persistence_dir, "events")
            if os.path.exists(events_dir):
                events_files = os.listdir(events_dir)
                assert len(events_files) > 0


def test_conversation_with_custom_id():
    """Test conversation creation with custom ID."""
    agent = create_test_agent()

    with tempfile.TemporaryDirectory() as tmpdir:
        custom_id = uuid.uuid4()
        conv = Conversation(
            agent=agent,
            persistence_dir=tmpdir,
            workspace=tmpdir,
            conversation_id=custom_id,
        )

        assert conv.id == custom_id
        assert conv.state.id == custom_id


def test_conversation_event_id_validation():
    """Test that EventLog prevents duplicate event IDs."""
    import pytest

    agent = create_test_agent()

    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(agent=agent, persistence_dir=tmpdir, workspace=tmpdir)

        # Add first event
        event1 = create_test_event("unique-id-1", "First event")
        conv.state.events.append(event1)

        # Add event with duplicate ID - should raise ValueError
        event2 = create_test_event("unique-id-1", "Second event")
        with pytest.raises(
            ValueError, match=r"Event with ID 'unique-id-1' already exists at index \d+"
        ):
            conv.state.events.append(event2)

        # Only the first event should be in the log
        our_events = [e for e in conv.state.events if e.id == "unique-id-1"]
        assert len(our_events) == 1


def _maybe_forked(test_func):
    # pytest-forked doesn't reliably compose with xdist; under xdist we already
    # have process isolation per worker, so avoid forking again.
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return test_func
    return pytest.mark.forked(test_func)


@_maybe_forked
def test_conversation_large_event_handling():
    """Test conversation handling of many events with memory usage monitoring."""
    import gc

    import psutil

    process = psutil.Process(os.getpid())
    initial_memory = process.memory_info().rss / 1024 / 1024  # MB

    agent = create_test_agent()

    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(agent=agent, persistence_dir=tmpdir, workspace=tmpdir)

        # Add many events to test memory bounds
        num_events = 5000  # Large number to test memory usage
        for i in range(num_events):
            event = create_test_event(f"bulk-event-{i:04d}", f"Message {i}")
            conv.state.events.append(event)

            # Check memory usage periodically
            if i % 1000 == 0 and i > 0:
                gc.collect()  # Force garbage collection

                assert process is not None
                current_memory = process.memory_info().rss / 1024 / 1024  # MB
                memory_growth = current_memory - initial_memory
                # Memory should not grow excessively (allow reasonable growth)
                assert memory_growth < 500, (
                    f"Memory usage grew too much: {memory_growth:.2f}MB "
                    f"after {i} events"
                )

        # Test that all events are accessible
        total_events = len(conv.state.events)
        assert total_events >= num_events

        # Find our test events
        our_events = [e for e in conv.state.events if e.id.startswith("bulk-event-")]
        assert len(our_events) == num_events

        # Test random access
        assert our_events[2500].id == "bulk-event-2500"
        assert our_events[4999].id == "bulk-event-4999"

        # Test iteration performance
        event_count = sum(
            1 for e in conv.state.events if e.id.startswith("bulk-event-")
        )
        assert event_count == num_events

        # Final memory check
        gc.collect()
        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        total_memory_growth = final_memory - initial_memory

        # Ensure memory usage stays bounded (allow reasonable growth)
        assert total_memory_growth < 1000, (
            f"Total memory growth too high: {total_memory_growth:.2f}MB "
            f"for {num_events} events"
        )
        print(
            f"Memory usage: initial {initial_memory:.2f}MB, "
            f"final {final_memory:.2f}MB, "
            f"growth {total_memory_growth:.2f}MB"
        )


def test_conversation_error_handling():
    """Test conversation handles errors gracefully."""
    agent = create_test_agent()

    with tempfile.TemporaryDirectory() as tmpdir:
        # Should create conversation with valid directories
        conv = Conversation(agent=agent, persistence_dir=tmpdir, workspace=tmpdir)

        # Should have basic properties
        assert conv.id is not None
        assert conv.state is not None


def test_conversation_memory_vs_local_filestore():
    """Test conversation works with different persistence configurations."""
    agent = create_test_agent()

    # Test with temporary directory (LocalFileStore)
    with tempfile.TemporaryDirectory() as temp_dir:
        conv = Conversation(agent=agent, persistence_dir=temp_dir, workspace=temp_dir)

        event = create_test_event("local-test", "Local test")
        conv.state.events.append(event)
        # State auto-saves when events are added

        # Verify files were created
        import os

        persistence_files = os.listdir(conv.state.persistence_dir)
        assert len(persistence_files) > 0
        assert any("base_state.json" in f for f in persistence_files)
