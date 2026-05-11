"""Stress test: a stalled subscriber must not OOM the server.

Bug class this catches:
    - Unbounded buffer growth when one subscriber stalls. In production this
      is a websocket client whose TCP buffer is full; in-process the
      analogue is a Subscriber that blocks indefinitely on each event.
    - Subscriber leak: a subscriber that's never unsubscribed accumulates
      across many events, even if individual events are small.

Why white-box (pub_sub) and not real websockets:
    Real WS through httpx.ASGITransport is awkward to drive; the failure
    mode (TCP buffer fills) only fires with real sockets. We exercise the
    closest in-process analogue — the Subscriber chain — and assert on
    invariants that *don't* depend on the TCP layer: subscriber registration
    is balanced, RSS stays bounded, fast subscribers don't see infinite
    delays merely because one subscriber is slow.

Architectural observation made testable here:
    PubSub.__call__ awaits subscribers sequentially (pub_sub.py:70-74). One
    slow subscriber blocks the chain. We assert ON THE CURRENT BEHAVIOUR
    (slow subscriber will hold up fast subscribers) — if a future refactor
    moves to per-subscriber tasks, the test will pass with much more
    headroom and budgets can be tightened.
"""

import asyncio
import os
import time
from dataclasses import dataclass, field

import psutil
import pytest

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_service import EventService
from openhands.agent_server.pub_sub import Subscriber
from openhands.sdk.event import Event
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from tests.agent_server.stress.budgets import SLOW_WEBSOCKET_CONSUMER
from tests.agent_server.stress.scripts import (
    SlowTestLLM,
    start_conversation_with_test_llm,
    text_message,
)


pytestmark = [pytest.mark.stress, pytest.mark.timeout(30)]


@dataclass(frozen=True, slots=True)
class _RecordingSubscriber(Subscriber[Event]):
    """Records every event it sees and the timestamp it saw it at."""

    events: list[tuple[float, type]] = field(default_factory=list)

    async def __call__(self, event: Event) -> None:
        self.events.append((time.monotonic(), type(event)))


@dataclass(slots=True)
class _StalledSubscriber(Subscriber[Event]):
    """Awaits forever inside __call__ — simulates a wedged consumer.

    The test releases ``unblock`` at teardown so any pending pub_sub publish
    coroutines can drain.
    """

    unblock: asyncio.Event = field(default_factory=asyncio.Event)
    seen_calls: int = 0

    async def __call__(self, event: Event) -> None:
        self.seen_calls += 1
        await self.unblock.wait()


async def _get_event_service(
    conversation_service: ConversationService, *, workspace_dir: str
) -> EventService:
    """Make an idle (un-run) conversation and return its EventService.

    The point of this test is to drive ``_pub_sub`` directly. If we let
    start_conversation auto-run (via initial_message), the placeholder LLM
    fires a real network call before our switch_llm lands, which both adds
    flake and blocks teardown.
    """
    parent_llm = SlowTestLLM.from_messages([text_message("done")], latency_s=0.0)
    info = await start_conversation_with_test_llm(
        conversation_service,
        parent_llm=parent_llm,
        workspace_dir=workspace_dir,
        usage_id="slow-ws",
        initial_text=None,
    )
    es = await conversation_service.get_event_service(info.id)
    assert es is not None
    return es


async def test_stalled_subscriber_does_not_grow_unbounded(
    conversation_service: ConversationService,
    tmp_path,
):
    """Fire N events while one subscriber stalls. Server RSS stays bounded;
    pub_sub registration is clean afterwards."""
    workspace = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    event_service = await _get_event_service(
        conversation_service, workspace_dir=workspace
    )

    baseline_subscribers = len(event_service._pub_sub._subscribers)

    proc = psutil.Process(os.getpid())
    # Take RSS *before* subscribing or publishing — this is the reference
    # point for the unbounded-growth budget. Sampling twice and using max
    # mitigates allocator noise from a single observation.
    rss_baseline_mb = max(proc.memory_info().rss / (1024 * 1024) for _ in range(3))

    # Subscribe via the underlying pub_sub directly, *not* via
    # event_service.subscribe_to_events. The latter performs an
    # initial-state-sync (event_service.py:412) that calls the new
    # subscriber synchronously — for the stalled subscriber that means it
    # blocks at registration time, never returns, and the test deadlocks.
    stalled = _StalledSubscriber()
    fast = _RecordingSubscriber()
    stalled_id = event_service._pub_sub.subscribe(stalled)
    fast_id = event_service._pub_sub.subscribe(fast)

    # Snapshot baseline event count: the conversation's state-change
    # callback may publish ambient events during startup. We measure delta.
    fast_baseline_events = len(fast.events)

    try:

        def _make_event(i: int) -> ConversationStateUpdateEvent:
            return ConversationStateUpdateEvent(
                key="execution_status",
                value=f"idle-{i}",
                source="environment",
            )

        async def _emit_one(i: int):
            await event_service._pub_sub(_make_event(i))

        # Each publish awaits the stalled subscriber forever (current
        # sequential pub_sub behaviour), so we fan out into background
        # tasks and let them queue up against the stall.
        publish_tasks = [
            asyncio.create_task(_emit_one(i))
            for i in range(SLOW_WEBSOCKET_CONSUMER.n_events)
        ]
        await asyncio.sleep(0.1)  # let create_task scheduling settle

        # Precondition check: the stalled subscriber must actually have
        # been invoked, otherwise the test passes for the wrong reason
        # (a regression that silently skips slow subscribers would let
        # everything drain instantly and the RSS / fast-subscriber
        # assertions below would all pass on a non-stalled chain).
        assert stalled.seen_calls > 0, (
            "stalled subscriber was never invoked; the publish chain isn't "
            "blocked on it. Did pub_sub start skipping subscribers?"
        )

        # Failure mode IS unbounded growth, so the budget is absolute.
        # Compare peak-during-stall against the pre-stall baseline. Same
        # max-of-3 sampling as the baseline so allocator noise doesn't
        # shrink the delta and mask real growth.
        rss_peak_mb = max(proc.memory_info().rss / (1024 * 1024) for _ in range(3))
        rss_delta = rss_peak_mb - rss_baseline_mb
        assert rss_delta < SLOW_WEBSOCKET_CONSUMER.max_rss_delta_mb, (
            f"RSS grew {rss_delta:.1f} MB with one stalled subscriber and "
            f"{SLOW_WEBSOCKET_CONSUMER.n_events} pending events "
            f"(baseline {rss_baseline_mb:.1f} → peak {rss_peak_mb:.1f}; "
            f"budget {SLOW_WEBSOCKET_CONSUMER.max_rss_delta_mb} MB). "
            f"Likely an unbounded per-subscriber buffer."
        )

        # Release the stall so publish_tasks can drain.
        stalled.unblock.set()
        await asyncio.gather(*publish_tasks)

        # The fast subscriber should have seen at least every event we
        # published. Ambient events from conversation lifecycle (state
        # update callbacks) may also flow through during the stall window
        # — those are fine; what we're catching is *dropped* events while
        # a sibling stalls.
        published = len(fast.events) - fast_baseline_events
        assert published >= SLOW_WEBSOCKET_CONSUMER.n_events, (
            f"fast subscriber received {published} of "
            f"{SLOW_WEBSOCKET_CONSUMER.n_events} published events. Events "
            f"were dropped while a sibling subscriber was stalled."
        )

    finally:
        # Cleanup must succeed even if assertions failed.
        stalled.unblock.set()
        event_service._pub_sub.unsubscribe(stalled_id)
        event_service._pub_sub.unsubscribe(fast_id)

    # Subscriber count returns to baseline after unsubscribing — the
    # registration accounting is balanced.
    assert len(event_service._pub_sub._subscribers) == baseline_subscribers, (
        f"after unsubscribing, pub_sub still has "
        f"{len(event_service._pub_sub._subscribers)} subscribers "
        f"(expected {baseline_subscribers}). Subscriber leak."
    )
