"""Sequence allocation survives independent processes and abrupt writer death."""

import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4

import pytest

from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.event import MessageEvent
from openhands.sdk.io import LocalFileStore
from openhands.sdk.llm import Message, TextContent


def message(parent: str | None = None) -> MessageEvent:
    return MessageEvent(
        id=str(uuid4()),
        parent_id=parent,
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="persisted")]),
    )


def process_writer(root, ready, release, count):
    log = EventLog(LocalFileStore(root))
    ready.put(True)
    if not release.wait(15):
        raise RuntimeError("parent did not release the writer")
    for _ in range(count):
        log.append(message())


def interrupted_writer(root, after_publish):
    store = LocalFileStore(root)
    original_write = store.write

    def interrupted(path, contents):
        if "/event-" in path:
            if after_publish:
                original_write(path, contents)
            os._exit(17)
        original_write(path, contents)

    with patch.object(store, "write", side_effect=interrupted):
        EventLog(store).append(message())


def interrupted_inflight_writer(root, value, reserved, publish):
    store = LocalFileStore(root)
    original_write = store.write

    def interrupted(path, contents):
        if "/event-00001-" in path:
            reserved.set()
            if not publish.wait(15):
                raise RuntimeError("parent did not release publication")
            original_write(path, contents)
            os._exit(17)
        original_write(path, contents)

    with patch.object(store, "write", side_effect=interrupted):
        EventLog(store).append(value)


@pytest.mark.parametrize("explicit_parent", [False, True])
def test_constructor_during_publication_refreshes_after_marker_cleanup_failure(
    tmp_path, explicit_parent
):
    store = LocalFileStore(str(tmp_path))
    writer = EventLog(store)
    first = message()
    writer.append(first)
    second = message(first.id)
    third = message(second.id if explicit_parent else None)
    reserved, publish = Event(), Event()
    original_write, original_delete = store.write, store.delete

    def gated_write(path, contents):
        if "/event-00001-" in path:
            reserved.set()
            assert publish.wait(10)
        original_write(path, contents)

    def fail_marker_delete(path):
        if path.endswith(".eventlog-len-1.marker"):
            raise OSError("count marker cleanup unavailable")
        original_delete(path)

    with (
        patch.object(store, "write", side_effect=gated_write),
        patch.object(store, "delete", side_effect=fail_marker_delete),
        ThreadPoolExecutor(max_workers=1) as workers,
    ):
        writing = workers.submit(writer.append, second)
        try:
            assert reserved.wait(10)
            resumed = EventLog(LocalFileStore(str(tmp_path)))
            assert [value.id for value in resumed] == [first.id]
        finally:
            publish.set()
        assert writing.result(timeout=10) == 1

    assert resumed.append(third) == 2
    assert [value.id for value in resumed] == [first.id, second.id, third.id]
    assert [resumed.get_seq(i) for i in range(3)] == [0, 1, 2]
    assert [value.id for value in EventLog(LocalFileStore(str(tmp_path)))] == [
        first.id,
        second.id,
        third.id,
    ]


@pytest.mark.parametrize("explicit_parent", [False, True])
def test_constructor_during_publication_refreshes_after_writer_process_death(
    tmp_path, explicit_parent
):
    first = message()
    EventLog(LocalFileStore(str(tmp_path))).append(first)
    second = message(first.id)
    third = message(second.id if explicit_parent else None)
    context = multiprocessing.get_context("spawn")
    reserved, publish = context.Event(), context.Event()
    worker = context.Process(
        target=interrupted_inflight_writer,
        args=(str(tmp_path), second, reserved, publish),
    )
    try:
        worker.start()
        assert reserved.wait(15)
        resumed = EventLog(LocalFileStore(str(tmp_path)))
        assert [value.id for value in resumed] == [first.id]
        publish.set()
        worker.join(timeout=20)
        assert worker.exitcode == 17
    finally:
        publish.set()
        if worker.is_alive():
            worker.terminate()
        if worker.pid is not None:
            worker.join(timeout=5)

    assert resumed.append(third) == 2
    assert [value.id for value in resumed] == [first.id, second.id, third.id]
    assert [resumed.get_seq(i) for i in range(3)] == [0, 1, 2]
    assert [value.id for value in EventLog(LocalFileStore(str(tmp_path)))] == [
        first.id,
        second.id,
        third.id,
    ]


def test_two_warm_processes_refresh_after_loss_and_a_same_count_append(tmp_path):
    initial = EventLog(LocalFileStore(str(tmp_path)))
    for _ in range(3):
        initial.append(message())
    lost = initial.get_id(1)
    context = multiprocessing.get_context("spawn")
    ready, release = context.Queue(), context.Event()
    workers = [
        context.Process(target=process_writer, args=(str(tmp_path), ready, release, 3))
        for _ in range(2)
    ]
    try:
        for worker in workers:
            worker.start()
        for _ in workers:
            assert ready.get(timeout=15)
        LocalFileStore(str(tmp_path)).delete(f"events/event-00001-{lost}.json")
        EventLog(LocalFileStore(str(tmp_path))).append(message())
        release.set()
        for worker in workers:
            worker.join(timeout=20)
            assert worker.exitcode == 0
    finally:
        release.set()
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            if worker.pid is not None:
                worker.join(timeout=5)
        ready.close()
        ready.join_thread()
    recovered = EventLog(LocalFileStore(str(tmp_path)))
    assert len(recovered) == 9
    assert [recovered.get_seq(i) for i in range(9)] == [0, *range(2, 10)]
    assert len({value.id for value in recovered}) == 9
    assert [value.id for value in recovered[recovered.index_after_seq(3) :]] == [
        recovered.get_id(i) for i in range(3, 9)
    ]


@pytest.mark.parametrize("after_publish", [False, True])
def test_process_death_releases_lock_without_reusing_reserved_sequence(
    tmp_path, after_publish
):
    context = multiprocessing.get_context("spawn")
    worker = context.Process(
        target=interrupted_writer, args=(str(tmp_path), after_publish)
    )
    try:
        worker.start()
        worker.join(timeout=20)
        assert worker.exitcode == 17
    finally:
        if worker.is_alive():
            worker.terminate()
        if worker.pid is not None:
            worker.join(timeout=5)
    recovered = EventLog(LocalFileStore(str(tmp_path)))
    assert len(recovered) == int(after_publish)
    appended = message()
    dense_index = recovered.append(appended)
    assert recovered.get_seq(dense_index) == 1
    again = EventLog(LocalFileStore(str(tmp_path)))
    assert again[-1].id == appended.id
    assert [again.get_seq(i) for i in range(len(again))] == (
        [0, 1] if after_publish else [1]
    )


def test_independent_threads_never_allocate_the_same_sequence_after_recovery(tmp_path):
    original = EventLog(LocalFileStore(str(tmp_path)))
    for _ in range(4):
        original.append(message())
    lost = original.get_id(2)
    barrier = Barrier(4)

    def append_batch(worker):
        log = EventLog(LocalFileStore(str(tmp_path)))
        barrier.wait(timeout=10)
        identifiers = []
        for _ in range(5):
            value = message()
            log.append(value)
            identifiers.append(value.id)
        return identifiers

    LocalFileStore(str(tmp_path)).delete(f"events/event-00002-{lost}.json")
    with ThreadPoolExecutor(max_workers=4) as workers:
        batches = list(workers.map(append_batch, range(4)))
    verified = EventLog(LocalFileStore(str(tmp_path)))
    assert len(verified) == 23
    assert [verified.get_seq(i) for i in range(len(verified))] == [0, 1, *range(3, 24)]
    assert {value.id for value in verified[3:]} == {
        identifier for batch in batches for identifier in batch
    }
    assert len(list(tmp_path.glob("events/event-*.json"))) == 23
