"""Deterministic tests for deferred (group-commit) durability — issue #5402.

Covers:
    - fsync executes on the per-store durability thread, never on the caller
      (event-loop) thread.
    - Writes are visible and recoverable after process exit without an
      explicit flush (write + rename stay synchronous).
    - Fault-injected fsync/os.replace failures propagate to callers
      (flush / fail-fast-before-mutation / inline) instead of being swallowed.
    - Event ordering, per-conversation isolation, and duplicate/parent
      validation are preserved with deferred durability enabled.
    - Explicit flush/shutdown semantics: run-end flush drains the queue,
      close() stops the worker thread.
"""

import asyncio
import multiprocessing
import os
import threading
import uuid
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.io import DurabilityError, LocalFileStore
from openhands.sdk.io.durability import DurabilityWriter
from openhands.sdk.io.memory import InMemoryFileStore
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.workspace import LocalWorkspace


def _event(event_id: str | None = None, content: str = "content") -> MessageEvent:
    # Default to a UUID id: only hex/dash ids round-trip through the on-disk
    # file-name scheme (EVENT_NAME_RE), which disk-reopen tests rely on.
    return MessageEvent(
        id=event_id or uuid.uuid4().hex,
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )


class _FsyncSpy:
    """Records (thread_name, fd) for every os.fsync call."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[str] = []
        self._orig = os.fsync

        def spy(fd: int) -> None:
            self.calls.append(threading.current_thread().name)
            self._orig(fd)

        monkeypatch.setattr(os, "fsync", spy)


def test_fsync_runs_on_durability_thread_not_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The event-loop responsiveness guarantee, stated without timing:
    no fsync may execute on the thread that calls write()."""
    spy = _FsyncSpy(monkeypatch)
    fs = LocalFileStore(str(tmp_path))
    try:
        fs.write("a/b.txt", "hello")
        caller = threading.current_thread().name
        fs.flush()
        assert len(spy.calls) == 1
        assert caller not in spy.calls
        assert all("durability" in t for t in spy.calls)
        assert fs.read("a/b.txt") == "hello"
    finally:
        fs.close()


def test_inline_mode_keeps_synchronous_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """deferred_durability=False preserves the previous inline behavior."""
    spy = _FsyncSpy(monkeypatch)
    fs = LocalFileStore(str(tmp_path), deferred_durability=False)
    fs.write("a.txt", "v")
    assert len(spy.calls) == 1
    assert spy.calls[0] == threading.current_thread().name


@pytest.mark.skipif(os.name != "posix", reason="process-exit test uses POSIX fork")
def test_write_visible_and_recoverable_without_flush(tmp_path: Path):
    """A real child exits with fsync pending; this is not a power-loss test."""
    ids = [uuid.uuid4().hex for _ in range(5)]

    def write_then_exit():
        entered = threading.Event()
        release = threading.Event()

        def pending_fsync(fd):
            entered.set()
            release.wait()

        os.fsync = pending_fsync
        log = EventLog(LocalFileStore(str(tmp_path)))
        for event_id in ids:
            log.append(_event(event_id))
        os._exit(0 if entered.wait(10) else 1)

    child = multiprocessing.get_context("fork").Process(target=write_then_exit)
    child.start()
    try:
        child.join(30)
        assert child.exitcode == 0, "child did not exit with fsync pending"
    finally:
        if child.is_alive():
            child.kill()
            child.join(10)

    fs2 = LocalFileStore(str(tmp_path))
    try:
        log2 = EventLog(fs2)
        assert len(log2) == 5
        for i, event_id in enumerate(ids):
            assert log2[i].id == event_id
        # Length marker matches the count of event files on disk.
        event_files = [
            p for p in fs2.list(log2._dir) if p.endswith(".json") and "event-" in p
        ]
        assert len(event_files) == 5
        assert fs2.exists(log2._marker_path(5))
    finally:
        fs2.close()


def test_fault_injected_fsync_propagates_and_fails_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A failing deferred fsync must (a) surface at flush and (b) fail the
    *next* write fast, before its mutation becomes visible on disk."""

    def boom(fd: int) -> None:
        raise OSError("injected fsync failure")

    fs = LocalFileStore(str(tmp_path))
    try:
        # Patch before writing so the queued fsync deterministically fails
        # (patching after write would race the worker thread).
        monkeypatch.setattr(os, "fsync", boom)
        fs.write("first.txt", "v")
        with pytest.raises(DurabilityError, match="Deferred durability failed"):
            fs.flush()
        # Fail-fast happens before the write mutates on-disk state.
        with pytest.raises(DurabilityError):
            fs.write("second.txt", "v")
        assert not fs.exists("second.txt")
        # The pre-failure write is intact and readable.
        assert fs.read("first.txt") == "v"
        # The recorded failure also propagates at close (not swallowed).
        with pytest.raises(DurabilityError):
            fs.close()
        with pytest.raises(DurabilityError):
            fs.close()
        with pytest.raises(RuntimeError, match="closed"):
            fs.write("late/file.txt", "v")
        assert not (tmp_path / "late").exists()
    finally:
        monkeypatch.undo()


def test_fault_injected_replace_raises_inline_without_partial_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """os.replace is synchronous even with deferred durability: its failure
    raises inline from append(), leaving neither a file nor an index entry."""
    fs = LocalFileStore(str(tmp_path))
    try:
        log = EventLog(fs)
        log.append(_event("ok-0"))
        monkeypatch.setattr(
            os, "replace", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("boom"))
        )
        with pytest.raises(OSError, match="boom"):
            log.append(_event("bad-1"))
        assert len(log) == 1
        assert "bad-1" not in log
        monkeypatch.undo()
        # The log still works afterwards.
        log.append(_event("ok-1"))
        assert len(log) == 2
    finally:
        fs.close()


def test_concurrent_appends_ordered_and_isolated(tmp_path: Path):
    """Concurrent appends to one log keep indices contiguous from 0; two
    logs on different stores stay isolated."""
    fs_a = LocalFileStore(str(tmp_path / "a"))
    fs_b = LocalFileStore(str(tmp_path / "b"))
    try:
        log_a, log_b = EventLog(fs_a), EventLog(fs_b)
        n_threads, n_per = 5, 10
        errors: list[BaseException] = []

        def produce(tid: int) -> None:
            try:
                for i in range(n_per):
                    log_a.append(_event(f"a-{tid}-{i}"))
                    log_b.append(_event(f"b-{tid}-{i}"))
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        threads = [
            threading.Thread(target=produce, args=(t,)) for t in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

        total = n_threads * n_per
        assert len(log_a) == total and len(log_b) == total
        ids_a = {log_a.get_id(i) for i in range(total)}
        ids_b = {log_b.get_id(i) for i in range(total)}
        assert all(i.startswith("a-") for i in ids_a)
        assert all(i.startswith("b-") for i in ids_b)
        # Indices are contiguous (get_id raises IndexError past the end).
        with pytest.raises(IndexError):
            log_a.get_id(total)
        fs_a.flush()
        fs_b.flush()
    finally:
        fs_a.close()
        fs_b.close()


def test_validation_errors_preserved_with_deferred_durability(tmp_path: Path):
    """Duplicate event IDs and missing explicit parents still raise."""
    fs = LocalFileStore(str(tmp_path))
    try:
        log = EventLog(fs)
        log.append(_event("root"))
        with pytest.raises(ValueError, match="already exists"):
            log.append(_event("root"))
        orphan = _event("orphan").model_copy(update={"parent_id": "missing"})
        with pytest.raises(ValueError, match="does not exist"):
            log.append(orphan)
        assert len(log) == 1
    finally:
        fs.close()


def test_marker_consistency_while_fsync_is_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """With the durability worker latched mid-fsync (simulated interruption
    window), a reopened log still sees contiguous events and a marker that
    matches the on-disk event count — renames are synchronous, only fsync
    lags."""
    entered = threading.Event()
    release = threading.Event()
    orig_fsync = os.fsync

    def latched(fd: int) -> None:
        entered.set()
        release.wait(timeout=10)
        orig_fsync(fd)

    fs = LocalFileStore(str(tmp_path))
    try:
        monkeypatch.setattr(os, "fsync", latched)
        log = EventLog(fs)
        ids = [uuid.uuid4().hex for _ in range(3)]
        for event_id in ids:
            log.append(_event(event_id))
        # Wait until the worker is parked inside the first fsync.
        assert entered.wait(timeout=10)

        fs2 = LocalFileStore(str(tmp_path))
        log2 = EventLog(fs2)
        assert len(log2) == 3
        assert [log2[i].id for i in range(3)] == ids
        assert fs2.exists(log2._marker_path(3))

        release.set()
        fs.flush()
    finally:
        release.set()
        monkeypatch.undo()
        fs.close()


def test_close_stops_worker_and_is_idempotent(tmp_path: Path):
    fs = LocalFileStore(str(tmp_path))
    fs.write("a.txt", "v")
    writer = fs._durability
    assert writer is not None and writer._thread is not None
    fs.close()
    assert writer._thread is not None and not writer._thread.is_alive()
    fs.close()  # idempotent
    with pytest.raises(RuntimeError, match="closed"):
        writer.submit_fsync(Path(tmp_path) / "a.txt")


def test_inmemory_store_flush_close_are_noops():
    fs = InMemoryFileStore()
    fs.write("a.txt", "v")
    fs.flush()
    fs.close()
    assert fs.read("a.txt") == "v"


def _make_conversation(tmp_path: Path, llm: LLM) -> LocalConversation:
    return Conversation(
        agent=Agent(llm=llm, tools=[]),
        persistence_dir=str(tmp_path / "persist"),
        workspace=str(tmp_path),
    )


def test_state_without_store_has_no_durability_work(tmp_path: Path):
    llm = TestLLM.from_messages([])
    state = ConversationState(
        id=uuid.uuid4(),
        agent=Agent(llm=llm, tools=[]),
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
    )
    state.flush()
    state.close()


def test_conversation_parent_chain_and_close_semantics(tmp_path: Path):
    """Stamped parent chain resolves to a single ordered branch; close()
    flushes and shuts down the durability writer."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="t")
    conv = _make_conversation(tmp_path, llm)
    for i in range(3):
        conv.send_message(Message(role="user", content=[TextContent(text=f"m{i}")]))
    persist_dir = conv._state.persistence_dir
    conv.close()

    # Reopen from disk: events contiguous from 0, branch chain intact.
    fs = LocalFileStore(str(persist_dir))
    try:
        log = EventLog(fs)
        branch = log.path_to_root(log.get_id(len(log) - 1))
        assert len(branch) == len(log), "branch must cover the whole linear log"
        texts = [
            c.text
            for e in branch
            if isinstance(e, MessageEvent)
            for c in e.llm_message.content
            if isinstance(c, TextContent)
        ]
        assert ["m0", "m1", "m2"] == [t for t in texts if t.startswith("m")]
    finally:
        fs.close()

    with pytest.raises(RuntimeError, match="closed"):
        conv._state._fs.write("late.txt", "v")


def test_run_end_flush_makes_all_events_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """At the end of run() every acknowledged event is durable (its fsync
    completed) before the conversation reports terminal — no explicit flush
    by the caller."""
    spy = _FsyncSpy(monkeypatch)
    llm = TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="done")])]
    )
    conv = _make_conversation(tmp_path, llm)
    conv.send_message(Message(role="user", content=[TextContent(text="hi")]))
    conv.run()

    events_dir = Path(str(conv._state.persistence_dir)) / "events"
    event_files = [p for p in os.listdir(events_dir) if p.startswith("event-")]
    assert len(event_files) >= 2  # user message + assistant response
    # Superseded/deleted markers may be skipped by the worker. Check thread
    # affinity and a drained queue, not a scheduling-dependent marker count.
    fsyncs_off_thread = [t for t in spy.calls if "durability" in t]
    assert len(fsyncs_off_thread) >= len(event_files)
    assert len(fsyncs_off_thread) == len(spy.calls)
    fs = conv._state._fs
    assert isinstance(fs, LocalFileStore)
    writer = fs._durability
    assert writer is None or writer._queue.unfinished_tasks == 0
    conv.close()


def test_close_cannot_overtake_accepted_submission(monkeypatch: pytest.MonkeyPatch):
    writer = DurabilityWriter()
    admitted = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    close_started = threading.Event()
    close_finished = threading.Event()
    errors: list[BaseException] = []
    original_put = writer._queue.put

    def latched_put(item):
        if item is not None:
            admitted.set()
            assert release.wait(10)
        original_put(item)

    def submit():
        try:
            writer.submit(completed.set)
        except BaseException as error:
            errors.append(error)

    def close():
        try:
            close_started.set()
            writer.close()
        except BaseException as error:
            errors.append(error)
        finally:
            close_finished.set()

    monkeypatch.setattr(writer._queue, "put", latched_put)
    producer = threading.Thread(target=submit, daemon=True)
    closer = threading.Thread(target=close, daemon=True)
    try:
        producer.start()
        assert admitted.wait(10)
        closer.start()
        assert close_started.wait(10)
        assert not close_finished.wait(0.1), "close overtook task admission"
    finally:
        release.set()
        producer.join(10)
        closer.join(10)
    assert not producer.is_alive() and not closer.is_alive()
    assert not errors
    assert completed.is_set(), "close dropped an accepted task"
    assert writer._queue.unfinished_tasks == 0
    with pytest.raises(RuntimeError, match="closed"):
        writer.submit(lambda: None)


def test_concurrent_close_drains_all_accepted_tasks_once():
    writer = DurabilityWriter()
    entered = threading.Event()
    release = threading.Event()
    results: list[int] = []
    errors: list[BaseException] = []

    def first():
        entered.set()
        assert release.wait(10)
        results.append(0)

    def close():
        try:
            writer.close()
        except BaseException as error:
            errors.append(error)

    writer.submit(first)
    for i in range(1, 20):
        writer.submit(lambda i=i: results.append(i))
    closers = [threading.Thread(target=close, daemon=True) for _ in range(2)]
    try:
        assert entered.wait(10)
        for closer in closers:
            closer.start()
    finally:
        release.set()
        for closer in closers:
            closer.join(10)
    assert all(not closer.is_alive() for closer in closers)
    assert not errors
    assert results == list(range(20))
    assert writer._queue.unfinished_tasks == 0
    writer.close()


def test_worker_cannot_deadlock_by_closing_itself():
    writer = DurabilityWriter()
    completed = threading.Event()
    writer.submit(writer.close)
    writer.submit(completed.set)
    with pytest.raises(DurabilityError) as error:
        writer.close()
    assert isinstance(error.value.__cause__, RuntimeError)
    assert "cannot close from its worker" in str(error.value.__cause__)
    assert completed.is_set()
    assert writer._queue.unfinished_tasks == 0


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("contents", ["text", b"binary"])
def test_store_close_rejects_writes_without_mutation(
    tmp_path: Path, deferred: bool, contents: str | bytes
):
    fs = LocalFileStore(str(tmp_path), deferred_durability=deferred)
    fs.close()  # Also covers closing a lazy, never-started writer.
    with pytest.raises(RuntimeError, match="closed"):
        fs.write("late/file.txt", contents)
    assert not (tmp_path / "late").exists()
    fs.close()


def test_store_close_waits_for_admitted_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from openhands.sdk.io import local

    fs = LocalFileStore(str(tmp_path))
    entered = threading.Event()
    release = threading.Event()
    fsynced = threading.Event()
    errors: list[BaseException] = []
    original_write = local.atomic_write_text
    close_started = threading.Event()
    close_finished = threading.Event()
    original_fsync = os.fsync

    def latched_write(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original_write(*args, **kwargs)

    def spy(fd):
        original_fsync(fd)
        fsynced.set()

    def write():
        try:
            fs.write("accepted.txt", "v")
        except BaseException as error:
            errors.append(error)

    def close():
        try:
            close_started.set()
            fs.close()
        except BaseException as error:
            errors.append(error)
        finally:
            close_finished.set()

    monkeypatch.setattr(local, "atomic_write_text", latched_write)
    monkeypatch.setattr(os, "fsync", spy)
    producer = threading.Thread(target=write, daemon=True)
    closer = threading.Thread(target=close, daemon=True)
    try:
        producer.start()
        assert entered.wait(10)
        closer.start()
        assert close_started.wait(10)
        assert not close_finished.wait(0.1), "close overtook the admitted write"
    finally:
        release.set()
        producer.join(10)
        closer.join(10)
    assert not producer.is_alive() and not closer.is_alive()
    assert not errors
    assert fsynced.is_set()
    assert fs.read("accepted.txt") == "v"
    with pytest.raises(RuntimeError, match="closed"):
        fs.write("late.txt", "v")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_arun_drains_durability_on_each_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
):
    llm = TestLLM.from_messages([Message(role="assistant", content=[])])
    conv = _make_conversation(tmp_path, llm)
    conv.send_message("hi")
    conv._state.flush()
    entered = threading.Event()
    release = threading.Event()
    original_fsync = os.fsync
    calls: list[str] = []

    def latched_fsync(fd):
        calls.append(threading.current_thread().name)
        entered.set()
        assert release.wait(10)
        original_fsync(fd)

    async def step(self, conversation, on_event, **kwargs):
        if outcome == "error":
            raise ValueError("primary run error")
        if outcome == "cancel":
            raise asyncio.CancelledError
        conversation.state.execution_status = ConversationExecutionStatus.FINISHED

    monkeypatch.setattr(os, "fsync", latched_fsync)
    monkeypatch.setattr(Agent, "astep", step)
    task = asyncio.create_task(conv.arun())
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        await asyncio.sleep(0)
        assert not task.done(), "arun returned before its fsync work completed"
        release.set()
        if outcome == "error":
            with pytest.raises(ConversationRunError) as error:
                await asyncio.wait_for(task, 10)
            assert isinstance(error.value.__cause__, ValueError)
        else:
            await asyncio.wait_for(task, 10)
            expected = (
                ConversationExecutionStatus.PAUSED
                if outcome == "cancel"
                else ConversationExecutionStatus.FINISHED
            )
            assert conv.state.execution_status == expected
        assert calls and threading.current_thread().name not in calls
        fs = conv._state._fs
        assert isinstance(fs, LocalFileStore)
        writer = fs._durability
        assert writer is not None and writer._queue.unfinished_tasks == 0
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        conv.close()


@pytest.mark.parametrize("async_run", [False, True])
@pytest.mark.asyncio
async def test_initialization_error_still_flushes_and_preserves_primary_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, async_run: bool
):
    conv = _make_conversation(tmp_path, TestLLM.from_messages([]))
    conv._state.flush()
    entered = threading.Event()
    release = threading.Event()

    def failing_fsync(fd):
        entered.set()
        assert release.wait(10)
        raise OSError("secondary durability failure")

    def failing_init(self):
        with self._state:
            self._state._fs.write("init.txt", "accepted")
        raise ValueError("primary initialization failure")

    monkeypatch.setattr(os, "fsync", failing_fsync)
    monkeypatch.setattr(LocalConversation, "_ensure_agent_ready", failing_init)
    task = asyncio.create_task(
        conv.arun() if async_run else asyncio.to_thread(conv.run)
    )
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        await asyncio.sleep(0)
        assert not task.done(), "initialization failure bypassed durability cleanup"
        release.set()
        with pytest.raises(ValueError, match="primary initialization failure"):
            await asyncio.wait_for(task, 10)
        fs = conv._state._fs
        assert isinstance(fs, LocalFileStore)
        writer = fs._durability
        assert writer is not None and writer._queue.unfinished_tasks == 0
        assert conv._arun_task is None
        assert conv._cancel_token is None
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        with pytest.raises(DurabilityError):
            conv.close()
        assert conv._cleanup_complete
        conv.close()  # Already closed despite reporting a durability failure.


@pytest.mark.asyncio
@pytest.mark.parametrize("init_fails", [False, True])
async def test_initialization_cancellation_settles_thread_and_durability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, init_fails: bool
):
    conv = _make_conversation(tmp_path, TestLLM.from_messages([]))
    conv._state.flush()
    initializing = threading.Event()
    finish_init = threading.Event()
    fsync_entered = threading.Event()
    finish_fsync = threading.Event()
    original_fsync = os.fsync

    def init(self):
        initializing.set()
        assert finish_init.wait(10)
        with self._state:
            self._state._fs.write("late-init.txt", "accepted")
        if init_fails:
            raise ValueError("initialization failed after cancellation")

    def latched_fsync(fd):
        fsync_entered.set()
        assert finish_fsync.wait(10)
        original_fsync(fd)

    monkeypatch.setattr(LocalConversation, "_ensure_agent_ready", init)
    monkeypatch.setattr(os, "fsync", latched_fsync)
    task = asyncio.create_task(conv.arun())
    try:
        assert await asyncio.to_thread(initializing.wait, 10)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # Repeated cancellation must not abandon the init thread.
        await asyncio.sleep(0)
        assert not task.done()
        finish_init.set()
        assert await asyncio.to_thread(fsync_entered.wait, 10)
        await asyncio.sleep(0)
        assert not task.done(), "cancellation bypassed the durability boundary"
        finish_fsync.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
        assert conv._state._fs.read("late-init.txt") == "accepted"
        fs = conv._state._fs
        assert isinstance(fs, LocalFileStore)
        writer = fs._durability
        assert writer is not None and writer._queue.unfinished_tasks == 0
        assert conv._arun_task is None
    finally:
        finish_init.set()
        finish_fsync.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        conv.close()


@pytest.mark.asyncio
async def test_cancellation_during_run_end_flush_does_not_abandon_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    conv = _make_conversation(tmp_path, TestLLM.from_messages([]))
    conv.send_message("hi")
    conv._state.flush()
    entered = threading.Event()
    release = threading.Event()
    step_done = asyncio.Event()
    original_fsync = os.fsync

    def latched_fsync(fd):
        entered.set()
        assert release.wait(10)
        original_fsync(fd)

    async def step(self, conversation, on_event, **kwargs):
        conversation.state.execution_status = ConversationExecutionStatus.FINISHED
        step_done.set()

    monkeypatch.setattr(os, "fsync", latched_fsync)
    monkeypatch.setattr(Agent, "astep", step)
    task = asyncio.create_task(conv.arun())
    try:
        await asyncio.wait_for(step_done.wait(), 10)
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done(), "cancelled flush abandoned accepted work"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
        fs = conv._state._fs
        assert isinstance(fs, LocalFileStore)
        writer = fs._durability
        assert writer is not None and writer._queue.unfinished_tasks == 0
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        conv.close()
