"""Edge cases for InMemoryFileStore."""

import threading

import pytest

from openhands.sdk.io.memory import InMemoryFileStore


def test_read_missing_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        InMemoryFileStore().read("missing.txt")


def test_write_bytes_decodes_utf8():
    store = InMemoryFileStore()

    store.write("a.txt", "café".encode())

    assert store.read("a.txt") == "café"


def test_exists_matches_directories_by_separator_not_prefix():
    store = InMemoryFileStore({"dir/file.txt": "x"})

    assert store.exists("dir")
    assert store.exists("dir/file.txt")
    assert not store.exists("di")


@pytest.mark.xfail(
    strict=True,
    reason="BUG: InMemoryFileStore.delete matches by string prefix, so "
    "deleting 'a/b' also deletes the sibling 'a/bc'",
)
def test_delete_does_not_remove_siblings_sharing_a_prefix():
    store = InMemoryFileStore({"a/b": "1", "a/bc": "2", "a/b/c": "3"})

    store.delete("a/b")

    assert store.read("a/bc") == "2"
    assert not store.exists("a/b")


@pytest.mark.xfail(
    strict=True,
    reason="BUG: InMemoryFileStore.list matches by string prefix, so listing "
    "'a' reports a phantom 'a/b/' directory for the file 'ab/c'",
)
def test_list_does_not_include_siblings_sharing_a_prefix():
    store = InMemoryFileStore({"a/b": "1", "ab/c": "2"})

    assert store.list("a") == ["a/b"]


@pytest.mark.xfail(
    strict=True,
    reason="BUG: a value larger than max_memory is silently not stored and "
    "the previous value is kept, so read returns stale content",
)
def test_oversize_write_is_not_silently_dropped():
    store = InMemoryFileStore(max_memory=16)
    store.write("state.json", "old")
    new_content = "x" * 32

    store.write("state.json", new_content)

    assert store.read("state.json") == new_content


def test_lock_times_out_while_another_thread_holds_it():
    store = InMemoryFileStore()
    held = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with store.lock("x"):
            held.set()
            release.wait(5)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    try:
        assert held.wait(5)
        with pytest.raises(TimeoutError):
            with store.lock("x", timeout=0.05):
                pass
    finally:
        release.set()
        holder.join(5)


def test_lock_is_released_when_body_raises():
    store = InMemoryFileStore()

    with pytest.raises(RuntimeError):
        with store.lock("x"):
            raise RuntimeError("boom")

    with store.lock("x", timeout=0.05):
        pass
