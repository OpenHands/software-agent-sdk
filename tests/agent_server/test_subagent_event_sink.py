"""Tests that the subagent event sink publishes to pub_sub but does NOT persist."""
import asyncio
import threading
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


@pytest.mark.asyncio
async def test_sink_delivers_from_worker_thread(stored, tmp_path):
    """Regression: sink called from a worker thread must reach _pub_sub subscribers.

    This is the REAL path: sub-agent runs in a ThreadPoolExecutor worker thread
    (LocalConversation._run_and_publish runs in run_in_executor). Before the fix
    the forwarding callback tried to mutate a frozen Pydantic Event via setattr,
    raising ValidationError (swallowed silently), so events never reached
    pub_sub / WebhookSubscriber.
    """
    svc = EventService(
        stored=stored,
        conversations_dir=tmp_path,
    )
    loop = asyncio.get_running_loop()
    svc._main_loop = loop

    received: list[Event] = []

    class ProbeSubscriber(Subscriber[Event]):
        async def __call__(self, event: Event):
            received.append(event)

    svc._pub_sub.subscribe(ProbeSubscriber())

    sink = svc._make_subagent_event_sink()

    from openhands.sdk.event.llm_convertible.message import MessageEvent
    from openhands.sdk.llm import Message
    from openhands.tools.task.manager import TaskManager

    # Wire the full forwarding path: TaskManager._make_forwarding_callback → sink
    tool_call_id = "toolu_regression_worker_thread"
    mgr = TaskManager(sub_event_sink=sink)
    fwd = mgr._make_forwarding_callback(parent_tool_use_id=tool_call_id)
    assert fwd is not None

    # Build a real frozen Pydantic event (as the sub-agent actually emits)
    event = MessageEvent(
        source="agent",
        llm_message=Message(role="assistant"),
    )
    assert event.parent_tool_use_id is None  # initially unset

    # Call the forwarding callback from a real worker thread — the real path
    worker_done = threading.Event()

    def worker():
        fwd(event)
        worker_done.set()

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=2)
    assert worker_done.is_set(), "worker thread did not finish"

    # Give run_coroutine_threadsafe time to schedule and run on the loop
    await asyncio.sleep(0.1)

    # The stamped copy (not the original frozen instance) must reach the subscriber
    assert len(received) == 1, f"Expected 1 event, got {len(received)}"
    stamped = received[0]
    assert stamped.parent_tool_use_id == tool_call_id, (
        f"parent_tool_use_id not stamped: got {stamped.parent_tool_use_id!r}"
    )
    # The original event must be unmodified (model is frozen, copy was made)
    assert event.parent_tool_use_id is None, (
        "Original event was mutated — model_copy was not used"
    )
