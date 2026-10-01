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

import os
import threading
import uuid
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.io import DurabilityError, LocalFileStore
from openhands.sdk.io.memory import InMemoryFileStore
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.testing import TestLLM


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
        assert spy.calls == [], "fsync must not run inline with deferred durability"
        fs.flush()
        assert len(spy.calls) == 1
        assert set(spy.calls) != {caller}
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


def test_write_visible_and_recoverable_without_flush(tmp_path: Path):
    """Process-exit recovery: write + rename are synchronous, so a reader
    that opens the store after a (simulated) process exit — no flush, no
    close — sees every acknowledged write."""
    fs = LocalFileStore(str(tmp_path))
    log = EventLog(fs)
    ids = [uuid.uuid4().hex for _ in range(5)]
    for event_id in ids:
        log.append(_event(event_id))

    # Simulate process exit: abandon the store without flush/close and
    # reopen a fresh store + log over the same directory.
    fs2 = LocalFileStore(str(tmp_path))
    try:
        log2 = EventLog(fs2)
        assert len(log2) == 5
        for i, event_id in enumerate(ids):
            assert log2[i].id == event_id
        # Length marker matches the count of event files on disk.
        event_files = [
            p
            for p in fs2.list(log2._dir)
            if p.endswith(".json") and "event-" in p
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
    assert fs._durability is None
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


def _make_conversation(tmp_path: Path, llm: LLM) -> Conversation:
    return Conversation(
        agent=Agent(llm=llm, tools=[]),
        persistence_dir=str(tmp_path / "persist"),
        workspace=str(tmp_path),
    )


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

    # close() shut the writer down (state fs is closed: writer released).
    assert conv._state._fs._durability is None


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
    # Every event file and every length marker got at least one fsync, and
    # none of them ran on this (caller) thread — run() returned only after
    # the durability queue drained.
    fsyncs_off_thread = [t for t in spy.calls if "durability" in t]
    assert len(fsyncs_off_thread) >= 2 * len(event_files)
    assert len(fsyncs_off_thread) == len(spy.calls)
    writer = conv._state._fs._durability
    assert writer is None or writer._queue.unfinished_tasks == 0
    conv.close()
