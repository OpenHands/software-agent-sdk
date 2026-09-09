from threading import Event

from flight_recorder.models.envelopes import CommittedBatch
from flight_recorder.services.subscriptions import TraceSubscription


def batch(first: int, last: int) -> CommittedBatch:
    return CommittedBatch(
        trace_id="trace",
        first_sequence=first,
        last_sequence=last,
        record_ids=tuple(str(sequence) for sequence in range(first, last + 1)),
    )


def test_subscription_resumes_after_sequence_and_unsubscribes() -> None:
    subscription = TraceSubscription()
    delivered: list[CommittedBatch] = []
    delivery = Event()

    def receive(item: CommittedBatch) -> None:
        delivered.append(item)
        delivery.set()

    handle = subscription.subscribe(
        "trace",
        after_sequence=5,
        callback=receive,
    )

    subscription.publish(batch(1, 5))
    subscription.publish(batch(6, 7))

    assert delivery.wait(1)
    assert [(item.first_sequence, item.last_sequence) for item in delivered] == [(6, 7)]
    handle.unsubscribe()
    handle.unsubscribe()
    subscription.publish(batch(8, 8))
    assert len(delivered) == 1
    subscription.close()


def test_slow_subscriber_does_not_block_fast_subscriber() -> None:
    subscription = TraceSubscription()
    release_slow = Event()
    slow_started = Event()
    fast_finished = Event()

    def slow_callback(item: CommittedBatch) -> None:
        slow_started.set()
        release_slow.wait(1)

    subscription.subscribe("trace", 0, slow_callback)
    subscription.subscribe("trace", 0, lambda item: fast_finished.set())

    subscription.publish(batch(1, 1))

    assert slow_started.wait(1)
    assert fast_finished.wait(1)
    release_slow.set()
    subscription.close()
