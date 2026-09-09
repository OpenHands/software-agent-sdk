"""Committed record subscription helpers."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock

from flight_recorder.models.envelopes import CommittedBatch


@dataclass
class SubscriptionHandle:
    _unsubscribe: Callable[[], None] | None

    def unsubscribe(self) -> None:
        if self._unsubscribe is not None:
            unsubscribe = self._unsubscribe
            self._unsubscribe = None
            unsubscribe()


@dataclass
class _Subscriber:
    trace_id: str
    after_sequence: int
    callback: Callable[[CommittedBatch], None]
    active: bool = True


class TraceSubscription:
    def __init__(self, max_workers: int = 8) -> None:
        self._subscribers: list[_Subscriber] = []
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="flight-recorder-subscription",
        )
        self._closed = False

    def subscribe(
        self,
        trace_id: str,
        after_sequence: int,
        callback: Callable[[CommittedBatch], None],
    ) -> SubscriptionHandle:
        subscriber = _Subscriber(trace_id, after_sequence, callback)
        with self._lock:
            if self._closed:
                raise RuntimeError("subscription service is closed")
            self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            with self._lock:
                subscriber.active = False

        return SubscriptionHandle(unsubscribe)

    def publish(self, batch: CommittedBatch) -> None:
        with self._lock:
            subscribers = [
                subscriber
                for subscriber in self._subscribers
                if subscriber.active
                and subscriber.trace_id == batch.trace_id
                and batch.last_sequence > subscriber.after_sequence
            ]
            for subscriber in subscribers:
                subscriber.after_sequence = batch.last_sequence
        for subscriber in subscribers:
            self._executor.submit(self._deliver, subscriber, batch)

    @staticmethod
    def _deliver(subscriber: _Subscriber, batch: CommittedBatch) -> None:
        if not subscriber.active:
            return
        try:
            subscriber.callback(batch)
        except Exception:
            pass

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for subscriber in self._subscribers:
                subscriber.active = False
        self._executor.shutdown(wait=True)
