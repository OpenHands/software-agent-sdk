"""Path matching for InMemoryFileStore."""

from openhands.sdk.io.memory import InMemoryFileStore


def test_delete_does_not_remove_siblings_sharing_a_prefix():
    store = InMemoryFileStore({"a/b": "1", "a/bc": "2", "a/b/c": "3"})

    store.delete("a/b")

    assert store.read("a/bc") == "2"
    assert not store.exists("a/b")


def test_list_does_not_include_siblings_sharing_a_prefix():
    store = InMemoryFileStore({"a/b": "1", "ab/c": "2"})

    assert store.list("a") == ["a/b"]


def test_oversize_write_is_not_silently_dropped():
    store = InMemoryFileStore(max_memory=16)
    store.write("state.json", "old")
    new_content = "x" * 32

    store.write("state.json", new_content)

    assert store.read("state.json") == new_content
