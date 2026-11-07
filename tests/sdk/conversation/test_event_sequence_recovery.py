"""Durable cursors remain unique when cooperating writers recover lost files."""

from pathlib import Path

import pytest

from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.io import InMemoryFileStore, LocalFileStore
from openhands.sdk.llm import Message, TextContent


def event(letter: str, parent: str | None = None) -> MessageEvent:
    return MessageEvent(
        id=f"{letter * 8}-0000",
        parent_id=parent,
        source="user",
        llm_message=Message(role="user", content=[TextContent(text=letter)]),
    )


@pytest.fixture(params=["local", "memory"])
def store(request: pytest.FixtureRequest, tmp_path: Path):
    if request.param == "local":
        return LocalFileStore(str(tmp_path))
    return InMemoryFileStore()


def lose(store, log: EventLog, index: int) -> None:
    seq, identifier = log.get_seq(index), log.get_id(index)
    store.delete(f"events/event-{seq:05d}-{identifier}.json")


def test_stale_writer_recognizes_parent_after_same_count_recovery(store):
    first = EventLog(store)
    for letter in "abc":
        first.append(event(letter))
    lose(store, first, 1)
    second = EventLog(store)
    second.append(event("d"))
    first.append(event("e", event("d").id))
    reloaded = EventLog(store)
    assert [value.id for value in reloaded] == [event(c).id for c in "acde"]
    assert [reloaded.get_seq(i) for i in range(len(reloaded))] == [0, 2, 3, 4]


def test_same_count_writer_recovery_never_duplicates_a_durable_cursor(store):
    first = EventLog(store)
    for letter in "abc":
        first.append(event(letter))
    lose(store, first, 1)
    second = EventLog(store)
    second.append(event("d"))
    first.append(event("e"))
    reloaded = EventLog(store)
    assert [reloaded.get_seq(i) for i in range(len(reloaded))] == [0, 2, 3, 4]
    assert [value.id for value in reloaded[reloaded.index_after_seq(3) :]] == [
        event("e").id
    ]


@pytest.mark.parametrize("lost_count", [1, 2, 3])
def test_lost_tail_does_not_reuse_a_cursor_that_clients_already_saw(store, lost_count):
    original = EventLog(store)
    for letter in "abc":
        original.append(event(letter))
    for index in range(3 - lost_count, 3):
        lose(store, original, index)
    reopened = EventLog(store)
    dense_index = reopened.append(event("d"))
    assert dense_index == 3 - lost_count
    assert reopened.get_seq(dense_index) == 3
    assert [value.id for value in reopened[reopened.index_after_seq(2) :]] == [
        event("d").id
    ]


def test_successive_tail_losses_keep_allocating_after_all_prior_reservations(store):
    log = EventLog(store)
    for letter in "abcde":
        index = log.append(event(letter))
        assert log.get_seq(index) == "abcde".index(letter)
        lose(store, log, index)
        log = EventLog(store)
    assert len(log) == 0


@pytest.mark.parametrize("failure_stage", ["reservation", "event", "published_event"])
def test_failed_publication_preserves_prior_events_and_allocated_sequences(
    store, monkeypatch: pytest.MonkeyPatch, failure_stage: str
):
    log = EventLog(store)
    log.append(event("a"))
    original_write = store.write

    def fail(path: str, contents: str | bytes) -> None:
        if failure_stage == "reservation" and ".eventlog-seq-00001" in path:
            raise OSError("reservation unavailable")
        if "event-00001-" in path:
            if failure_stage == "published_event":
                original_write(path, contents)
            raise OSError("event publication interrupted")
        original_write(path, contents)

    with monkeypatch.context() as patch:
        patch.setattr(store, "write", fail)
        with pytest.raises(OSError):
            log.append(event("b"))
    reopened = EventLog(store)
    assert reopened[0].id == event("a").id
    index = reopened.append(event("c"))
    assert reopened.get_seq(index) == (1 if failure_stage == "reservation" else 2)
    expected = "abc" if failure_stage == "published_event" else "ac"
    assert [value.id for value in reopened] == [event(c).id for c in expected]
    assert len(set(reopened.get_seq(i) for i in range(len(reopened)))) == len(reopened)


def test_interruption_after_reserving_a_sequence_leaves_a_gap_not_reuse(
    store, monkeypatch: pytest.MonkeyPatch
):
    log = EventLog(store)
    original_write = store.write

    def interrupt(path: str, contents: str | bytes) -> None:
        if "/event-" in path:
            raise KeyboardInterrupt()
        original_write(path, contents)

    with monkeypatch.context() as patch:
        patch.setattr(store, "write", interrupt)
        with pytest.raises(KeyboardInterrupt):
            log.append(event("a"))
    assert len(EventLog(store)) == 0
    recovered = EventLog(store)
    index = recovered.append(event("b"))
    assert index == 0
    assert recovered.get_seq(index) == 1


def test_live_writer_refreshes_after_another_writer_reserves_but_cannot_publish(
    store, monkeypatch: pytest.MonkeyPatch
):
    old = EventLog(store)
    old.append(event("a"))
    second = EventLog(store)
    original_write = store.write

    def fail_event(path: str, contents: str | bytes) -> None:
        if "/event-00001-" in path:
            raise OSError("lost write")
        original_write(path, contents)

    with monkeypatch.context() as patch:
        patch.setattr(store, "write", fail_event)
        with pytest.raises(OSError):
            second.append(event("b"))
    index = old.append(event("c"))
    assert old.get_seq(index) == 2
    reloaded = EventLog(store)
    assert [value.id for value in reloaded] == [event(c).id for c in "ac"]
    assert [reloaded.get_seq(i) for i in range(len(reloaded))] == [0, 2]


def test_rejected_duplicate_or_parent_does_not_consume_a_sequence(store):
    log = EventLog(store)
    log.append(event("a"))
    with pytest.raises(ValueError, match="already exists"):
        log.append(event("a"))
    with pytest.raises(ValueError, match="Parent event"):
        log.append(event("b", event("f").id))
    index = log.append(event("b", event("a").id))
    assert index == 1 and log.get_seq(index) == 1


def test_failed_write_guard_does_not_allocate_or_publish(store):
    from contextlib import contextmanager

    log = EventLog(store)

    @contextmanager
    def deny_write():
        raise PermissionError("ownership lost")
        yield

    log.set_write_guard(deny_write)
    with pytest.raises(PermissionError, match="ownership lost"):
        log.append(event("a"))
    assert len(EventLog(store)) == 0
    log.set_write_guard(None)
    index = log.append(event("b"))
    assert index == 0 and log.get_seq(index) == 0


def test_failed_initial_scan_cannot_make_a_writer_overwrite_unknown_history(
    store, monkeypatch: pytest.MonkeyPatch
):
    initial = EventLog(store)
    initial.append(event("a"))
    initial.append(event("b"))
    with monkeypatch.context() as patch:
        patch.setattr(
            store,
            "list",
            lambda path: (_ for _ in ()).throw(PermissionError("unreadable")),
        )
        writer = EventLog(store)
        with pytest.raises(PermissionError, match="unreadable"):
            writer.append(event("c"))
    assert [value.id for value in EventLog(store)] == [event(c).id for c in "ab"]
    index = writer.append(event("c"))
    assert writer.get_seq(index) == 2


def test_failed_refresh_retains_last_known_state_and_recovers_after_storage_returns(
    store, monkeypatch: pytest.MonkeyPatch
):
    first, second = EventLog(store), EventLog(store)
    first.append(event("a"))
    second.append(event("b"))
    with monkeypatch.context() as patch:
        patch.setattr(
            store,
            "list",
            lambda path: (_ for _ in ()).throw(OSError("temporary outage")),
        )
        with pytest.raises(OSError, match="temporary outage"):
            first.append(event("c"))
    assert first[0].id == event("a").id
    first.append(event("c"))
    assert [value.id for value in EventLog(store)] == [event(c).id for c in "abc"]


def test_count_marker_deletion_failure_does_not_hide_another_writer(
    store, monkeypatch: pytest.MonkeyPatch
):
    old = EventLog(store)
    for letter in "abc":
        old.append(event(letter))
    lose(store, old, 1)
    reopened = EventLog(store)
    original_delete = store.delete

    def fail_marker_delete(path: str) -> None:
        if ".eventlog-len-" in path:
            raise OSError("stale count marker cannot be removed")
        original_delete(path)

    with monkeypatch.context() as patch:
        patch.setattr(store, "delete", fail_marker_delete)
        reopened.append(event("d"))
        old.append(event("e", event("d").id))
    verified = EventLog(store)
    assert [verified.get_seq(i) for i in range(len(verified))] == [0, 2, 3, 4]


def test_legacy_length_hint_preserves_a_deleted_tail_without_rewriting_events(store):
    original = EventLog(store)
    for letter in "abc":
        original.append(event(letter))
    for path in store.list("events"):
        if ".eventlog-seq-" in path:
            store.delete(path)
    before = {value.id: value.model_dump_json() for value in original}
    lose(store, original, 2)
    migrated = EventLog(store)
    index = migrated.append(event("d"))
    assert migrated.get_seq(index) == 3
    assert migrated[0].model_dump_json() == before[event("a").id]
    assert migrated[1].model_dump_json() == before[event("b").id]
    lose(store, migrated, index)
    migrated = EventLog(store)
    index = migrated.append(event("e"))
    assert migrated.get_seq(index) == 4


def test_unknown_sidecars_are_not_sequence_allocations(store):
    for name in [
        ".eventlog-seq-99999.marker.tmp",
        ".eventlog-seq-xxxxx.marker",
        ".other-seq-99999.marker",
    ]:
        store.write(f"events/{name}", "")
    log = EventLog(store)
    index = log.append(event("a"))
    assert log.get_seq(index) == 0


def test_writer_without_marker_access_falls_back_to_a_complete_scan(
    store, monkeypatch: pytest.MonkeyPatch
):
    first = EventLog(store)
    first.append(event("a"))
    second = EventLog(store)
    second.append(event("b"))
    original_exists = store.exists

    def unavailable(path: str) -> bool:
        if ".marker" in path:
            raise OSError("marker probe unavailable")
        return original_exists(path)

    with monkeypatch.context() as patch:
        patch.setattr(store, "exists", unavailable)
        first.append(event("c"))
    assert [value.id for value in EventLog(store)] == [event(c).id for c in "abc"]


def test_a_store_that_reports_a_missing_directory_can_publish_its_first_event(
    store, monkeypatch: pytest.MonkeyPatch
):
    original_list = store.list

    def missing_directory(path: str) -> list[str]:
        if not store.exists(path):
            raise FileNotFoundError(path)
        return original_list(path)

    monkeypatch.setattr(store, "list", missing_directory)
    log = EventLog(store)
    index = log.append(event("a"))
    assert index == 0 and log.get_seq(index) == 0
    assert EventLog(store)[0].id == event("a").id
