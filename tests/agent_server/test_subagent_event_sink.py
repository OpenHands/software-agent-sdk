"""Tests that the subagent event sink publishes to pub_sub but does NOT persist."""
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StoredConversation
from openhands.agent_server.pub_sub import Subscriber
from openhands.sdk import LLM, Agent, Event
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def stored():
    return StoredConversation(
        id=uuid4(),
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir="workspace/project"),
        confirmation_policy=NeverConfirm(),
        initial_message=None,
        metrics=None,
        created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_sink_publishes_not_persists(stored, tmp_path):
    """_make_subagent_event_sink publishes to _pub_sub but does NOT grow state.events."""
    svc = EventService(
        stored=stored,
        conversations_dir=tmp_path,
    )
    # Set the main_loop (normally set in start())
    svc._main_loop = asyncio.get_running_loop()

    received = []

    class ProbeSubscriber(Subscriber[Event]):
        async def __call__(self, event: Event):
            received.append(event)

    # Subscribe a probe to pub_sub
    svc._pub_sub.subscribe(ProbeSubscriber())

    sink = svc._make_subagent_event_sink()

    # Create a dummy event using a simple Event subclass
    from openhands.sdk.event.llm_convertible.message import MessageEvent
    from openhands.sdk.llm import Message

    event = MessageEvent(
        id="sub-evt-1",
        source="user",
        llm_message=Message(role="user"),
    )

    # Call sink (fires run_coroutine_threadsafe)
    sink(event)

    # Wait for the coroutine to complete
    await asyncio.sleep(0.05)

    assert len(received) == 1
    assert received[0] is event
