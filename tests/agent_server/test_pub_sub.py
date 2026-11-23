import asyncio
from uuid import uuid4

import pytest

from openhands.agent_server.pub_sub import MaxSubscribersError, PubSub, Subscriber


class RecordingSubscriber(Subscriber[str]):
    def __init__(
        self,
        *,
        call_gate: asyncio.Event | None = None,
        fail_on_call: bool = False,
        fail_on_close: bool = False,
    ) -> None:
        self.events: list[str] = []
        self.call_gate = call_gate
        self.fail_on_call = fail_on_call
        self.fail_on_close = fail_on_close
        self.closed = False

    async def __call__(self, event: str) -> None:
        self.events.append(event)
        if self.call_gate is not None:
            await self.call_gate.wait()
        if self.fail_on_call:
            raise RuntimeError("subscriber failed")

    async def close(self) -> None:
        self.closed = True
        if self.fail_on_close:
            raise RuntimeError("close failed")


def test_subscribe_rejects_subscribers_at_limit() -> None:
    pub_sub = PubSub[str](max_subscribers=1)
    pub_sub.subscribe(RecordingSubscriber())

    with pytest.raises(MaxSubscribersError, match=r"Subscriber limit reached \(1\)"):
        pub_sub.subscribe(RecordingSubscriber())


def test_unsubscribe_frees_capacity_and_reports_unknown_ids() -> None:
    pub_sub = PubSub[str](max_subscribers=1)
    subscriber_id = pub_sub.subscribe(RecordingSubscriber())

    assert pub_sub.unsubscribe(subscriber_id)
    assert not pub_sub.unsubscribe(subscriber_id)
    assert not pub_sub.unsubscribe(uuid4())
    pub_sub.subscribe(RecordingSubscriber())


async def test_dispatch_reaches_only_current_subscribers() -> None:
    pub_sub = PubSub[str]()
    remaining = RecordingSubscriber()
    removed = RecordingSubscriber()
    pub_sub.subscribe(remaining)
    removed_id = pub_sub.subscribe(removed)

    await pub_sub("before")
    pub_sub.unsubscribe(removed_id)
    await pub_sub("after")

    assert remaining.events == ["before", "after"]
    assert removed.events == ["before"]


async def test_slow_subscriber_does_not_block_others() -> None:
    gate = asyncio.Event()
    slow = RecordingSubscriber(call_gate=gate)
    fast = RecordingSubscriber()
    pub_sub = PubSub[str]()
    pub_sub.subscribe(slow)
    pub_sub.subscribe(fast)

    dispatch = asyncio.create_task(pub_sub("event"))
    await asyncio.wait_for(_wait_for_events(fast, 1), timeout=1)

    assert not dispatch.done()
    gate.set()
    await dispatch


async def _wait_for_events(subscriber: RecordingSubscriber, count: int) -> None:
    while len(subscriber.events) < count:
        await asyncio.sleep(0)


async def test_failing_subscriber_does_not_block_others() -> None:
    failing = RecordingSubscriber(fail_on_call=True)
    healthy = RecordingSubscriber()
    pub_sub = PubSub[str]()
    pub_sub.subscribe(failing)
    pub_sub.subscribe(healthy)

    await pub_sub("event")

    assert failing.events == ["event"]
    assert healthy.events == ["event"]


async def test_close_clears_subscribers_despite_close_failure() -> None:
    failing = RecordingSubscriber(fail_on_close=True)
    healthy = RecordingSubscriber()
    pub_sub = PubSub[str]()
    pub_sub.subscribe(failing)
    pub_sub.subscribe(healthy)

    await pub_sub.close()

    assert failing.closed
    assert healthy.closed
    assert pub_sub.subscriber_ids() == set()
