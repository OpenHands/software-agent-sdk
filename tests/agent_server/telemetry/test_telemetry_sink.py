"""Delivery guarantees: non-blocking, bounded, and safely revocable."""

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

import pytest

from openhands.agent_server.telemetry import models as m
from openhands.agent_server.telemetry.sink import (
    BufferedTelemetrySink,
    NoOpTelemetrySink,
)


def make_event(index: int = 0) -> m.DiagnosticEvent:
    return m.DiagnosticEvent(
        event_name=m.CONVERSATION_STARTED,
        occurred_at=datetime.now(UTC),
        distinct_id=f"user-{index}",
        runtime=m.RuntimeProperties(
            server_version="1.36.1",
            sdk_version="1.36.1",
            tools_version="1.36.1",
            build_git_sha="unknown",
            build_git_ref="unknown",
            python_version="3.13",
            platform="darwin",
            deployment_mode="local_opt_in",
            deferred_init=False,
        ),
        properties=m.ConversationStartedProperties(
            conversation_ref="a" * 32,
            llm_model_family="anthropic",
            agent_kind="agent",
            tool_count=index,
            is_fork=False,
            has_agent_profile=False,
            workspace_kind="localworkspace",
            confirmation_mode=False,
        ),
    )


class RecordingExporter:
    def __init__(self):
        self.batches: list[list[m.DiagnosticEvent]] = []
        self.closed = False

    async def send(self, events):
        self.batches.append(list(events))

    async def aclose(self):
        self.closed = True

    @property
    def sent(self) -> list[m.DiagnosticEvent]:
        return [e for batch in self.batches for e in batch]


class HangingExporter:
    def __init__(self):
        self.calls = 0

    async def send(self, events):
        self.calls += 1
        await asyncio.sleep(3600)

    async def aclose(self):
        pass


class FailingExporter:
    def __init__(self):
        self.calls = 0

    async def send(self, events):
        self.calls += 1
        raise RuntimeError("downstream is down")

    async def aclose(self):
        pass


def build_sink(exporter, **kwargs: Any) -> BufferedTelemetrySink:
    defaults: dict[str, Any] = {
        "mode": "local_opt_in",
        "consent": "granted",
        "flush_delay": 0.05,
        "event_buffer_size": 10,
        "num_retries": 0,
        "retry_delay": 0.0,
        # Kept short so only the test that is *about* the shutdown bound pays
        # for it; that test overrides this back to a realistic value.
        "shutdown_flush_timeout": 0.1,
    }
    defaults.update(kwargs)
    return BufferedTelemetrySink(exporter, **defaults)


# ── non-blocking ──────────────────────────────────────────────────────────


async def test_emit_never_awaits_even_when_the_exporter_hangs():
    """The core guarantee: PubSub fan-out cannot be stalled by telemetry.

    The exporter sleeps for an hour. If ``emit`` did any I/O, or awaited the
    exporter at all, this would time out instead of returning instantly.
    """
    sink = build_sink(HangingExporter())
    sink.start()
    try:
        started = time.monotonic()
        for i in range(100):
            sink.emit(make_event(i))
        elapsed = time.monotonic() - started

        assert elapsed < 0.05, f"emit() blocked for {elapsed:.3f}s"
    finally:
        await asyncio.wait_for(sink.aclose(), timeout=10)


async def test_emit_is_a_plain_sync_function():
    """A coroutine here would silently reintroduce blocking at the call site."""
    assert not asyncio.iscoroutinefunction(BufferedTelemetrySink.emit)
    assert not asyncio.iscoroutinefunction(NoOpTelemetrySink.emit)


# ── bounded queue ─────────────────────────────────────────────────────────


async def test_queue_is_bounded_on_ingest_and_keeps_the_newest():
    exporter = HangingExporter()
    sink = build_sink(exporter, max_queue_size=5, flush_delay=3600)
    try:
        for i in range(50):
            sink.emit(make_event(i))

        assert len(sink._queue) == 5
        # Oldest dropped, newest retained.
        retained = [e.distinct_id for e in sink._queue]
        assert retained == [f"user-{i}" for i in range(45, 50)]
        assert sink.dropped_count == 45
    finally:
        await asyncio.wait_for(sink.aclose(), timeout=10)


async def test_a_backlog_drains_continuously_not_one_batch_per_flush():
    """Regression: a queue deeper than one batch must not stall between batches.

    Previously ``_wake`` was cleared before taking a batch and never re-armed
    while the queue was still non-empty, so each remaining batch waited a full
    ``flush_delay``. At the production defaults (30s delay, 20-event batch) a
    full 1000-event queue would have needed ~25 minutes to clear, dropping
    events it could have delivered.
    """
    exporter = RecordingExporter()
    sink = build_sink(exporter, flush_delay=0.5, event_buffer_size=10)
    sink.start()
    try:
        for i in range(50):
            sink.emit(make_event(i))

        # Well under two flush_delays: a stalling drain would deliver ~10-20.
        await asyncio.sleep(0.7)

        assert len(exporter.sent) == 50, (
            f"only {len(exporter.sent)}/50 delivered in one flush cycle; "
            "the drain loop is waiting out flush_delay between batches"
        )
        assert len(sink._queue) == 0
    finally:
        await asyncio.wait_for(sink.aclose(), timeout=10)


# ── exporter failure ──────────────────────────────────────────────────────


async def test_failing_exporter_does_not_kill_the_drain_task():
    exporter = FailingExporter()
    sink = build_sink(exporter, retry_delay=0.01)
    sink.start()
    try:
        sink.emit(make_event())
        await asyncio.sleep(0.3)

        assert exporter.calls >= 1
        assert sink._drain_task is not None
        assert not sink._drain_task.done(), "drain task died on exporter failure"
    finally:
        await asyncio.wait_for(sink.aclose(), timeout=10)


async def test_failed_batches_are_dropped_not_requeued():
    """Re-queueing would turn an outage into unbounded memory growth."""
    exporter = FailingExporter()
    sink = build_sink(exporter, retry_delay=0.01)
    sink.start()
    try:
        sink.emit(make_event())
        await asyncio.sleep(0.3)
        assert len(sink._queue) == 0
    finally:
        await asyncio.wait_for(sink.aclose(), timeout=10)


async def test_shutdown_is_bounded_when_the_exporter_hangs():
    """A wedged analytics endpoint must not extend server shutdown."""
    sink = build_sink(HangingExporter(), flush_delay=3600, shutdown_flush_timeout=1.0)
    sink.start()
    sink.emit(make_event())

    started = time.monotonic()
    await asyncio.wait_for(sink.aclose(), timeout=15)
    elapsed = time.monotonic() - started

    # Capped by shutdown_flush_timeout, not by the exporter's 3600s sleep.
    assert 0.5 < elapsed < 4, f"shutdown took {elapsed:.1f}s"


async def test_healthy_shutdown_flushes_residual_events():
    exporter = RecordingExporter()
    sink = build_sink(exporter, flush_delay=3600)
    sink.start()
    sink.emit(make_event(1))
    sink.emit(make_event(2))

    await asyncio.wait_for(sink.aclose(), timeout=10)

    assert len(exporter.sent) == 2
    assert exporter.closed is True


# ── consent ───────────────────────────────────────────────────────────────


async def test_revocation_discards_the_queue_and_never_delivers():
    """Events collected under a consent since withdrawn must not be sent."""
    exporter = RecordingExporter()
    sink = build_sink(exporter, flush_delay=3600)
    sink.start()
    try:
        for i in range(5):
            sink.emit(make_event(i))
        assert len(sink._queue) == 5

        sink.on_consent_changed("denied")

        assert sink.enabled is False
        assert len(sink._queue) == 0

        await asyncio.sleep(0.2)
        assert exporter.batches == [], "revoked events were delivered"
    finally:
        await asyncio.wait_for(sink.aclose(), timeout=10)
        assert exporter.batches == [], "revoked events were flushed on close"


async def test_emit_while_denied_is_dropped_and_cannot_leak_on_later_grant():
    """Re-granting must not retroactively ship pre-consent activity."""
    exporter = RecordingExporter()
    sink = build_sink(exporter, consent="denied", flush_delay=0.05)
    sink.start()
    try:
        assert sink.enabled is False
        for i in range(5):
            sink.emit(make_event(i))
        assert len(sink._queue) == 0

        sink.on_consent_changed("granted")
        await asyncio.sleep(0.2)

        assert exporter.sent == [], "pre-consent events leaked after grant"

        sink.emit(make_event(99))
        await asyncio.sleep(0.2)
        assert [e.distinct_id for e in exporter.sent] == ["user-99"]
    finally:
        await asyncio.wait_for(sink.aclose(), timeout=10)


async def test_cloud_locked_ignores_a_denial():
    exporter = RecordingExporter()
    sink = build_sink(exporter, mode="cloud_locked", consent="denied")
    try:
        assert sink.enabled is True
        sink.on_consent_changed("denied")
        assert sink.enabled is True
    finally:
        await asyncio.wait_for(sink.aclose(), timeout=10)


async def test_consent_is_refreshed_from_the_reader_on_the_drain_task():
    exporter = RecordingExporter()
    state = {"consent": "granted"}

    async def reader():
        return state["consent"]

    sink = build_sink(exporter, consent="granted", consent_reader=reader)
    sink.start()
    try:
        state["consent"] = "denied"
        sink.emit(make_event())
        # Wait past CONSENT_REFRESH_SECONDS-gated first check.
        await asyncio.sleep(0.3)

        assert sink.enabled is False
    finally:
        await asyncio.wait_for(sink.aclose(), timeout=10)


# ── no-op ─────────────────────────────────────────────────────────────────


async def test_noop_sink_is_inert():
    sink = NoOpTelemetrySink()
    assert sink.enabled is False
    sink.emit(make_event())
    await sink.aclose()


@pytest.mark.parametrize("mode", ["disabled"])
async def test_disabled_mode_never_enables(mode):
    sink = build_sink(RecordingExporter(), mode=mode, consent="granted")
    try:
        assert sink.enabled is False
    finally:
        await asyncio.wait_for(sink.aclose(), timeout=10)
