"""Regression tests for idle-clock resets driven by streaming deltas (#4695).

#4689 made streaming deltas opt-in for PubSub subscribers. ``_EventSubscriber``
does not opt in, so a long streamed completion stops refreshing the runtime
idle clocks and the runtime-api can reap the pod mid-stream. These tests pin
the contract: deltas reach the subscriber, reset the shared clocks on a
throttled cadence, and durable events keep resetting them immediately.
"""

import time
from unittest.mock import MagicMock, patch

from openhands.agent_server.conversation_service import (
    _DELTA_ACTIVITY_SIGNAL_INTERVAL,
    _EventSubscriber,
)
from openhands.agent_server.pub_sub import PubSub
from openhands.sdk import Message
from openhands.sdk.event import MessageEvent, StreamingDeltaEvent
from openhands.sdk.llm import TextContent


def _delta(content: str = "tok") -> StreamingDeltaEvent:
    return StreamingDeltaEvent(content=content)


async def _publish(ps: PubSub, event) -> None:
    await ps(event)


async def test_subscriber_receives_streaming_deltas():
    """The subscriber must opt in to deltas or it never sees any activity."""
    service = MagicMock()
    subscriber = _EventSubscriber(service=service)
    ps = PubSub()
    ps.subscribe(subscriber)

    with patch(
        "openhands.agent_server.conversation_service.update_last_execution_time"
    ) as reset:
        await _publish(ps, _delta())

    assert reset.call_count == 1
    service.touch.assert_called_once()


async def test_delta_resets_are_throttled():
    """Per-token resets are throttled; each delta still refreshes touch()."""
    service = MagicMock()
    subscriber = _EventSubscriber(service=service)
    ps = PubSub()
    ps.subscribe(subscriber)

    with patch(
        "openhands.agent_server.conversation_service.update_last_execution_time"
    ) as reset:
        # First delta ever: elapsed since the epoch-default is huge, so it fires.
        await _publish(ps, _delta("a"))
        assert reset.call_count == 1

        # Deltas arriving within the throttle window do not fire again, but the
        # per-conversation eviction clock is still refreshed per delta.
        for i in range(10):
            await _publish(ps, _delta(f"b{i}"))
        assert reset.call_count == 1
        assert service.touch.call_count == 11

        # A delta arriving after the throttle window fires exactly once.
        subscriber._last_delta_reset = (
            time.monotonic() - _DELTA_ACTIVITY_SIGNAL_INTERVAL - 0.01
        )
        await _publish(ps, _delta("c"))
        assert reset.call_count == 2
        assert service.touch.call_count == 12


async def test_delta_within_threshold_keeps_idle_time_below_it():
    """Deltas flowing at ≥ the throttle cadence bound idle_time far below the
    ~20 min runtime-api pod-reap threshold: consecutive resets are never more
    than one throttle interval apart."""

    # Simulate deltas arriving every 10 seconds (faster than the throttle) for
    # 25 simulated minutes: each delta lands `_last_delta_reset` far enough in
    # the past to fire the next reset, mirroring a steady token stream.
    recorded: list[float] = []
    fake_now = time.monotonic()
    service = MagicMock()
    subscriber = _EventSubscriber(service=service)
    ps = PubSub()
    ps.subscribe(subscriber)

    with patch(
        "openhands.agent_server.conversation_service.update_last_execution_time"
    ) as reset:
        for _ in range(150):  # 25 minutes / 10 seconds
            fake_now += 10.0
            # The delta sees the clock at fake_now; the previous reset happened
            # one interval before it, so the throttle window has elapsed.
            subscriber._last_delta_reset = fake_now - (
                _DELTA_ACTIVITY_SIGNAL_INTERVAL + 1.0
            )
            with patch(
                "openhands.agent_server.conversation_service.time.monotonic",
                return_value=fake_now,
            ):
                await _publish(ps, _delta())
            recorded.append(reset.call_count and fake_now)

    assert len(recorded) == 150
    gaps = [b - a for a, b in zip(recorded, recorded[1:])]
    assert gaps and max(gaps) <= _DELTA_ACTIVITY_SIGNAL_INTERVAL + 1.0
    # 30s throttle ≪ ~20 min pod-reap threshold: idle_time never grows stale.
    assert max(gaps) < 20 * 60


async def test_durable_event_resets_immediately():
    """Non-delta events keep the original immediate reset behavior."""
    service = MagicMock()
    subscriber = _EventSubscriber(service=service)
    ps = PubSub()
    ps.subscribe(subscriber)

    event = MessageEvent(
        id="evt-1",
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="hello")]),
    )
    with patch(
        "openhands.agent_server.conversation_service.update_last_execution_time"
    ) as reset:
        await _publish(ps, event)
        # A durable event fires even immediately after a delta reset.
        subscriber._last_delta_reset = time.monotonic()
        await _publish(ps, event)

    assert reset.call_count == 2
    assert service.touch.call_count == 2
    assert service.stored.updated_at is not None
