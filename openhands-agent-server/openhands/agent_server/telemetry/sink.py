"""Bounded, asynchronous delivery that can never block or fail a conversation.

The load-bearing decision is that :meth:`TelemetrySink.emit` is **synchronous**.
``PubSub.__call__`` *awaits* its subscribers inside an ``asyncio.gather``, so a
subscriber that performs inline network I/O stalls event fan-out for every
sibling subscriber on that conversation. ``emit`` therefore appends to an
in-memory deque and returns within a single event-loop step; all I/O happens on
a separate long-lived drain task.

Failure policy differs deliberately from ``WebhookSubscriber``:

* the queue is bounded **on ingest** via ``deque(maxlen=…)`` rather than only
  on the retry path, so a wedged exporter cannot grow memory without limit;
* a batch that exhausts its retries is **dropped, not re-queued** — re-queueing
  converts a downstream outage into unbounded memory plus a queue permanently
  full of stale events;
* revocation **discards** the queue instead of draining it.
"""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Final, Protocol

from openhands.agent_server.telemetry.models import DiagnosticEvent
from openhands.agent_server.telemetry.policy import TelemetryDecision
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

#: Consent cache TTL. Revocation does not wait for it; the endpoint pushes.
CONSENT_REFRESH_SECONDS: Final = 5.0

#: Caps the final flush so a hung endpoint cannot extend shutdown.
SHUTDOWN_FLUSH_TIMEOUT_SECONDS: Final = 5.0

_MAX_BACKOFF_SECONDS: Final = 60.0


class TelemetryExporter(Protocol):
    """Ships a batch somewhere. The only vendor-aware seam."""

    async def send(self, events: list[DiagnosticEvent]) -> None: ...

    async def aclose(self) -> None: ...


class TelemetrySink(Protocol):
    """What the rest of the server depends on. Never vendor-specific."""

    @property
    def enabled(self) -> bool: ...

    def emit(self, event: DiagnosticEvent) -> None:
        """Accept an event. Must be non-blocking and must never raise."""
        ...

    def on_decision_changed(self, decision: TelemetryDecision) -> None:
        """Apply a re-resolved consent decision immediately."""
        ...

    async def aclose(self) -> None: ...


class NoOpTelemetrySink:
    """The default. Discards everything, cheaply."""

    @property
    def enabled(self) -> bool:
        return False

    # Args ignored; names match the protocol for keyword callers.
    def emit(self, event: DiagnosticEvent) -> None:  # noqa: ARG002
        return None

    def on_decision_changed(self, decision: TelemetryDecision) -> None:  # noqa: ARG002
        return None

    async def aclose(self) -> None:
        return None


class BufferedTelemetrySink:
    """Queue-and-drain sink with bounded memory and isolated failures."""

    def __init__(
        self,
        exporter: TelemetryExporter,
        *,
        decision: TelemetryDecision,
        decision_reader: Callable[[], Awaitable[TelemetryDecision]] | None = None,
        max_queue_size: int = 1000,
        event_buffer_size: int = 20,
        flush_delay: float = 30.0,
        num_retries: int = 2,
        retry_delay: float = 5.0,
        shutdown_flush_timeout: float = SHUTDOWN_FLUSH_TIMEOUT_SECONDS,
    ) -> None:
        self._exporter = exporter
        self._shutdown_flush_timeout = shutdown_flush_timeout
        self._decision: TelemetryDecision = decision
        self._decision_reader = decision_reader
        self._event_buffer_size = max(1, event_buffer_size)
        self._flush_delay = max(0.1, flush_delay)
        self._num_retries = max(0, num_retries)
        self._retry_delay = max(0.0, retry_delay)

        self._queue: deque[DiagnosticEvent] = deque(maxlen=max(1, max_queue_size))
        self._wake = asyncio.Event()
        self._closed = False
        self._dropped_count = 0
        self._failure_streak = 0
        self._consent_checked_at: float | None = None
        self._drain_task: asyncio.Task | None = None

    def start(self) -> None:
        """Spawn the drain task. Retained on the instance, never orphaned."""
        if self._drain_task is None:
            self._drain_task = asyncio.create_task(
                self._drain_loop(), name="telemetry-drain"
            )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._wake.set()

        if self._drain_task is not None:
            self._drain_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._drain_task
            self._drain_task = None

        if self._decision.enabled and self._queue:
            batch = list(self._queue)
            self._queue.clear()
            with suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(
                    self._exporter.send(batch),
                    timeout=self._shutdown_flush_timeout,
                )

        self._queue.clear()
        with suppress(Exception):
            await self._exporter.aclose()

    @property
    def enabled(self) -> bool:
        return self._decision.enabled and not self._closed

    @property
    def decision(self) -> TelemetryDecision:
        return self._decision

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    def on_decision_changed(self, decision: TelemetryDecision) -> None:
        """Apply a re-resolved decision immediately.

        Called synchronously from the settings-update path so revocation takes
        effect without waiting for the refresh interval. On revocation the
        queue is **discarded rather than flushed** — the inverse of
        ``WebhookSubscriber.close()``. Events collected under a consent that
        has since been withdrawn must not be delivered.
        """
        self._apply_decision(decision)

    def _apply_decision(self, decision: TelemetryDecision) -> None:
        was_enabled = self._decision.enabled
        self._decision = decision
        if was_enabled and not decision.enabled:
            discarded = len(self._queue)
            self._queue.clear()
            logger.info(
                "Telemetry disabled (%s); discarded %d queued event(s)",
                decision.reason,
                discarded,
            )

    async def _refresh_consent_if_stale(self) -> None:
        """Re-read persisted consent on the drain task, never inline.

        The settings store is a file read, so it must not happen on the hot
        path in :meth:`emit`.
        """
        if self._decision_reader is None:
            return

        loop = asyncio.get_running_loop()
        now = loop.time()
        if (
            self._consent_checked_at is not None
            and now - self._consent_checked_at < CONSENT_REFRESH_SECONDS
        ):
            return
        self._consent_checked_at = now

        try:
            decision = await self._decision_reader()
        except Exception as exc:
            logger.debug("Telemetry consent refresh failed: %s", type(exc).__name__)
            return

        if decision != self._decision:
            self._apply_decision(decision)

    def emit(self, event: DiagnosticEvent) -> None:
        """Enqueue an event. Synchronous, non-blocking, never raises.

        A ``deque`` with ``maxlen`` drops the oldest element on overflow in
        O(1) and cannot raise, so there is no failure mode to propagate back
        into conversation execution.
        """
        if not self.enabled:
            return
        try:
            if len(self._queue) == self._queue.maxlen:
                self._dropped_count += 1
            self._queue.append(event)
            self._wake.set()
        except Exception:  # pragma: no cover - defensive, deque cannot raise
            logger.debug("Telemetry emit failed", exc_info=True)

    async def _drain_loop(self) -> None:
        while not self._closed:
            try:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=self._flush_delay)
                self._wake.clear()

                await self._refresh_consent_if_stale()

                if not self._decision.enabled:
                    self._queue.clear()
                    continue

                batch = self._take_batch()
                if batch:
                    await self._dispatch(batch)
                    if self._queue:
                        # Keep draining; do not wait out flush_delay per batch.
                        self._wake.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("Telemetry drain iteration failed", exc_info=True)
                await asyncio.sleep(self._retry_delay)

    def _take_batch(self) -> list[DiagnosticEvent]:
        batch: list[DiagnosticEvent] = []
        while self._queue and len(batch) < self._event_buffer_size:
            batch.append(self._queue.popleft())
        return batch

    async def _dispatch(self, batch: list[DiagnosticEvent]) -> None:
        for attempt in range(self._num_retries + 1):
            # Re-check: a revocation may have landed mid-retry.
            if not self._decision.enabled:
                return
            try:
                await self._exporter.send(batch)
                self._failure_streak = 0
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if attempt < self._num_retries:
                    await asyncio.sleep(self._retry_delay)
                    continue
                self._failure_streak += 1
                # One warning per streak, so an outage doesn't flood the log.
                if self._failure_streak == 1:
                    logger.warning(
                        "Telemetry export failing (%s); dropping %d event(s). "
                        "Further failures logged at debug level.",
                        type(exc).__name__,
                        len(batch),
                    )
                else:
                    logger.debug(
                        "Telemetry export failure #%d (%s)",
                        self._failure_streak,
                        type(exc).__name__,
                    )
                self._dropped_count += len(batch)
                # Capped: this sleeps on the drain task.
                backoff = min(
                    self._retry_delay * (2 ** min(self._failure_streak, 4)),
                    _MAX_BACKOFF_SECONDS,
                )
                await asyncio.sleep(backoff)
                return
