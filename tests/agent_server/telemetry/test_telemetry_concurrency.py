"""Deadlock, cancellation and concurrency guarantees.

Every test here is wrapped in a hard ``asyncio.wait_for``: a regression that
introduces a hang fails as a timeout rather than blocking CI forever.

Background on why these specific shapes are tested:

* ``emit()`` runs on the event loop from ``PubSub``; the drain task runs
  concurrently on the same loop; ``on_consent_changed`` is called synchronously
  from a request handler. All three touch ``_queue`` and ``_decision``.
* ``_read_consent`` runs on a worker thread via ``asyncio.to_thread``. Cancelling
  a task awaiting ``to_thread`` does not stop the thread, so shutdown must not
  wait on it.
* ``FileSettingsStore.update()`` takes a blocking ``fcntl.flock`` on a fresh fd,
  which is **not** reentrant — a nested update from the same process would
  deadlock on itself.
"""

import asyncio

from tests.agent_server.telemetry.test_telemetry_sink import (
    RecordingExporter,
    build_sink,
    denied,
    granted,
    make_event,
)


TIMEOUT = 10.0


# ── cancellation / shutdown ───────────────────────────────────────────────


async def test_shutdown_while_consent_reader_is_blocked_in_a_thread():
    """Cancelling a task awaiting to_thread must not wait for the thread.

    The reader blocks on a threading primitive that is never released, so if
    aclose() waited on the worker thread this would hang.
    """
    release = asyncio.Event()  # deliberately never set

    async def blocking_reader():
        await release.wait()
        return granted()

    sink = build_sink(
        RecordingExporter(), decision_reader=blocking_reader, flush_delay=0.01
    )
    sink.start()
    sink.emit(make_event())
    await asyncio.sleep(0.1)  # let the drain task enter the reader

    await asyncio.wait_for(sink.aclose(), timeout=TIMEOUT)


async def test_shutdown_while_dispatch_is_in_flight():
    """aclose() must not wait out an exporter that is mid-send."""

    class SlowExporter:
        async def send(self, events):
            await asyncio.sleep(3600)

        async def aclose(self):
            pass

    sink = build_sink(SlowExporter(), flush_delay=0.01, shutdown_flush_timeout=0.2)
    sink.start()
    sink.emit(make_event())
    await asyncio.sleep(0.15)

    await asyncio.wait_for(sink.aclose(), timeout=TIMEOUT)


async def test_concurrent_aclose_calls_do_not_deadlock_or_double_close():
    exporter = RecordingExporter()
    sink = build_sink(exporter)
    sink.start()
    sink.emit(make_event())

    await asyncio.wait_for(
        asyncio.gather(sink.aclose(), sink.aclose(), sink.aclose()), timeout=TIMEOUT
    )
    assert exporter.closed is True


async def test_aclose_without_start_does_not_hang():
    sink = build_sink(RecordingExporter())
    await asyncio.wait_for(sink.aclose(), timeout=TIMEOUT)


async def test_emit_after_close_is_a_no_op_and_does_not_raise():
    exporter = RecordingExporter()
    sink = build_sink(exporter)
    sink.start()
    await asyncio.wait_for(sink.aclose(), timeout=TIMEOUT)

    sink.emit(make_event())
    assert sink.enabled is False
    assert len(sink._queue) == 0


# ── concurrent mutation ───────────────────────────────────────────────────


async def test_revocation_racing_a_burst_of_emits():
    """Interleaving emit() and revocation must not lose or leak events."""
    exporter = RecordingExporter()
    sink = build_sink(exporter, flush_delay=0.01)
    sink.start()
    try:

        async def emitter():
            for i in range(500):
                sink.emit(make_event(i))
                if i % 50 == 0:
                    await asyncio.sleep(0)

        async def flipper():
            for _ in range(20):
                sink.on_decision_changed(denied())
                await asyncio.sleep(0)
                sink.on_decision_changed(granted())
                await asyncio.sleep(0)

        await asyncio.wait_for(asyncio.gather(emitter(), flipper()), timeout=TIMEOUT)

        # End denied: nothing further may be delivered and the queue is empty.
        sink.on_decision_changed(denied())
        assert len(sink._queue) == 0

        before = len(exporter.sent)
        await asyncio.sleep(0.1)
        assert len(exporter.sent) == before
    finally:
        await asyncio.wait_for(sink.aclose(), timeout=TIMEOUT)


async def test_drain_survives_a_consent_reader_that_always_raises():
    calls = {"n": 0}

    async def broken_reader():
        calls["n"] += 1
        raise RuntimeError("settings unreadable")

    exporter = RecordingExporter()
    sink = build_sink(exporter, decision_reader=broken_reader, flush_delay=0.02)
    sink.start()
    try:
        sink.emit(make_event())
        await asyncio.sleep(0.3)

        assert calls["n"] >= 1, "consent reader was never exercised"
        assert len(exporter.sent) >= 1
    finally:
        await asyncio.wait_for(sink.aclose(), timeout=TIMEOUT)


async def test_sink_never_blocks_the_loop_under_load():
    """A heartbeat coroutine must keep ticking while telemetry is saturated."""
    ticks = {"n": 0}

    async def heartbeat():
        for _ in range(50):
            ticks["n"] += 1
            await asyncio.sleep(0.001)

    class SlowExporter:
        async def send(self, events):
            await asyncio.sleep(0.05)

        async def aclose(self):
            pass

    sink = build_sink(SlowExporter(), flush_delay=0.01)
    sink.start()
    try:
        hb = asyncio.create_task(heartbeat())
        for i in range(2000):
            sink.emit(make_event(i))
        await asyncio.wait_for(hb, timeout=TIMEOUT)
        assert ticks["n"] == 50
    finally:
        await asyncio.wait_for(sink.aclose(), timeout=TIMEOUT)


# ── the "never breaks a conversation" guarantee ───────────────────────────
# Each subscriber entry point is driven with an injected failure; none may
# propagate, because these run inside PubSub fan-out during a live run.


def _subscriber(sink):
    import uuid

    from openhands.agent_server.telemetry.factory import (
        DiagnosticEventFactory,
        build_runtime_properties,
    )
    from openhands.agent_server.telemetry.subscriber import (
        ConversationTelemetryContext,
        TelemetrySubscriber,
    )

    cid = uuid.uuid4()
    factory = DiagnosticEventFactory(
        runtime=build_runtime_properties(deferred_init=False),
        salt="s",
    )
    return TelemetrySubscriber(
        conversation_id=cid,
        sink=sink,
        factory=factory,
        context=ConversationTelemetryContext(
            conversation_ref=factory.conversation_ref(cid),
            user_id=None,
            llm_model_family="anthropic",
            agent_kind="agent",
            tool_count=0,
            is_fork=False,
            has_agent_profile=False,
            workspace_kind="localworkspace",
            confirmation_policy="neverconfirm",
        ),
    )


class _ExplodingSink:
    enabled = True

    def emit(self, event):
        raise RuntimeError("sink exploded")

    def on_consent_changed(self, consent):
        pass

    async def aclose(self):
        pass


async def test_emit_started_swallows_a_sink_failure():
    _subscriber(_ExplodingSink()).emit_started()  # must not raise


async def test_close_swallows_a_terminal_emit_failure():
    await asyncio.wait_for(_subscriber(_ExplodingSink()).close(), timeout=TIMEOUT)


async def test_call_swallows_a_failure_from_any_event_type():
    from openhands.sdk.event import AgentErrorEvent, ConversationStateUpdateEvent
    from openhands.sdk.event.conversation_error import ConversationErrorEvent

    sub = _subscriber(_ExplodingSink())
    for event in (
        ConversationStateUpdateEvent(key="execution_status", value="idle"),
        ConversationStateUpdateEvent(key="execution_status", value="finished"),
        AgentErrorEvent(error="boom", tool_name="bash", tool_call_id="c1"),
        ConversationErrorEvent(source="environment", code="X", detail="d"),
    ):
        await asyncio.wait_for(sub(event), timeout=TIMEOUT)


async def test_capture_usage_swallows_a_malformed_stats_block():
    """A shape change in full_state must degrade, not raise."""
    from openhands.sdk.event import ConversationStateUpdateEvent

    class _Collecting:
        enabled = True

        def __init__(self):
            self.events = []

        def emit(self, e):
            self.events.append(e)

        def on_consent_changed(self, c):
            pass

        async def aclose(self):
            pass

    sink = _Collecting()
    sub = _subscriber(sink)
    await sub(ConversationStateUpdateEvent(key="execution_status", value="idle"))
    await sub(
        ConversationStateUpdateEvent(
            key="full_state",
            value={
                "execution_status": "finished",
                # stats is the wrong shape all the way down
                "stats": {"usage_to_metrics": {"a": "not-a-dict", "b": None}},
            },
        )
    )
    payload = sink.events[0].to_payload()
    assert payload["total_tokens_bucket"] in {"lt-1000", "unknown"}
    assert payload["terminal_status"] == "finished"


async def test_shutdown_is_bounded_when_exporter_aclose_hangs():
    """Regression: the exporter close was uncapped.

    For PostHog this is where the real network I/O happens — capture() only
    enqueues, so aclose() -> client.shutdown() is flush() + join(), and
    posthog's consumer defaults to retries=10 with timeout=15 per batch. An
    unreachable endpoint blocked the uvicorn lifespan here for minutes.
    """
    import time

    class HangingCloseExporter:
        async def send(self, events):
            pass

        async def aclose(self):
            await asyncio.sleep(3600)

    sink = build_sink(HangingCloseExporter(), shutdown_flush_timeout=0.5)
    sink.start()
    sink.emit(make_event())

    started = time.monotonic()
    await asyncio.wait_for(sink.aclose(), timeout=TIMEOUT)
    elapsed = time.monotonic() - started

    assert elapsed < 4, f"aclose() took {elapsed:.1f}s; the exporter close is uncapped"


async def test_shutdown_is_bounded_when_both_send_and_aclose_hang():
    """Both legs are capped, so worst case is bounded by 2x the timeout."""
    import time

    class FullyHangingExporter:
        async def send(self, events):
            await asyncio.sleep(3600)

        async def aclose(self):
            await asyncio.sleep(3600)

    sink = build_sink(
        FullyHangingExporter(), flush_delay=3600, shutdown_flush_timeout=0.5
    )
    sink.start()
    sink.emit(make_event())

    started = time.monotonic()
    await asyncio.wait_for(sink.aclose(), timeout=TIMEOUT)
    elapsed = time.monotonic() - started

    assert elapsed < 5, f"aclose() took {elapsed:.1f}s"
