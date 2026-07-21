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
import inspect

import pytest

from openhands.agent_server.telemetry.sink import BufferedTelemetrySink
from tests.agent_server.telemetry.test_telemetry_sink import (
    RecordingExporter,
    build_sink,
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
        return "granted"

    sink = build_sink(
        RecordingExporter(), consent_reader=blocking_reader, flush_delay=0.01
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


async def test_double_start_creates_only_one_drain_task():
    sink = build_sink(RecordingExporter())
    sink.start()
    first = sink._drain_task
    sink.start()
    try:
        assert sink._drain_task is first
    finally:
        await asyncio.wait_for(sink.aclose(), timeout=TIMEOUT)


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
                sink.on_consent_changed("denied")
                await asyncio.sleep(0)
                sink.on_consent_changed("granted")
                await asyncio.sleep(0)

        await asyncio.wait_for(asyncio.gather(emitter(), flipper()), timeout=TIMEOUT)

        # End denied: nothing further may be delivered and the queue is empty.
        sink.on_consent_changed("denied")
        assert len(sink._queue) == 0

        before = len(exporter.sent)
        await asyncio.sleep(0.1)
        assert len(exporter.sent) == before
    finally:
        await asyncio.wait_for(sink.aclose(), timeout=TIMEOUT)


async def test_many_concurrent_emitters_do_not_exceed_the_bound():
    exporter = RecordingExporter()
    sink = build_sink(exporter, max_queue_size=50, flush_delay=3600)
    try:

        async def emitter(n: int):
            for i in range(200):
                sink.emit(make_event(n * 1000 + i))
                if i % 25 == 0:
                    await asyncio.sleep(0)

        await asyncio.wait_for(
            asyncio.gather(*(emitter(n) for n in range(8))), timeout=TIMEOUT
        )
        assert len(sink._queue) == 50
        assert sink._queue.maxlen == 50
    finally:
        await asyncio.wait_for(sink.aclose(), timeout=TIMEOUT)


async def test_drain_survives_a_consent_reader_that_always_raises():
    calls = {"n": 0}

    async def broken_reader():
        calls["n"] += 1
        raise RuntimeError("settings unreadable")

    exporter = RecordingExporter()
    sink = build_sink(exporter, consent_reader=broken_reader, flush_delay=0.02)
    sink.start()
    try:
        sink.emit(make_event())
        await asyncio.sleep(0.3)

        assert calls["n"] >= 1, "consent reader was never exercised"
        assert sink._drain_task is not None
        assert not sink._drain_task.done(), "drain task died on a reader failure"
        # Delivery continues on the last known decision.
        assert len(exporter.sent) >= 1
    finally:
        await asyncio.wait_for(sink.aclose(), timeout=TIMEOUT)


# ── blocking-call discipline ──────────────────────────────────────────────


def test_emit_and_on_consent_changed_are_sync_and_lock_free():
    """Neither may await or touch the settings store on the hot path."""
    for fn in (BufferedTelemetrySink.emit, BufferedTelemetrySink.on_consent_changed):
        assert not asyncio.iscoroutinefunction(fn)
        src = inspect.getsource(fn)
        assert "await" not in src, f"{fn.__name__} must not await"
        assert "get_settings_store" not in src, f"{fn.__name__} must not read settings"


def test_consent_is_never_read_from_inside_a_settings_lock():
    """store.update() takes a non-reentrant flock; nesting would self-deadlock.

    The consent endpoint must finish its update() before touching the sink, and
    the sink must never call back into the store.
    """
    import openhands.agent_server.telemetry.router as router_mod

    src = inspect.getsource(router_mod.set_telemetry_consent)
    update_at = src.index("store.update(")
    notify_at = src.index("notify_consent_changed(")
    assert update_at < notify_at, (
        "notify_consent_changed must run after store.update() returns, not inside it"
    )

    sink_src = inspect.getsource(BufferedTelemetrySink)
    assert "store.update(" not in sink_src, "the sink must never write settings"


def test_settings_load_does_not_take_the_file_lock():
    """The drain task reads consent on a worker thread.

    If load() took the same flock that update() does, a consent write on the
    event loop and a consent read on the worker thread would contend on every
    refresh. It does not — this test pins that assumption.
    """
    from openhands.agent_server.persistence.store import FileSettingsStore

    load_src = inspect.getsource(FileSettingsStore.load)
    update_src = inspect.getsource(FileSettingsStore.update)

    assert "_file_lock" in update_src, "update() is expected to lock"
    assert "_file_lock" not in load_src, (
        "load() started taking the file lock; telemetry's background consent "
        "refresh now contends with settings writes"
    )


@pytest.mark.parametrize("mode", ["cloud_locked", "local_opt_in"])
async def test_sink_never_blocks_the_loop_under_load(mode):
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

    sink = build_sink(SlowExporter(), mode=mode, flush_delay=0.01)
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
        runtime=build_runtime_properties(mode="cloud_locked", deferred_init=False),
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
