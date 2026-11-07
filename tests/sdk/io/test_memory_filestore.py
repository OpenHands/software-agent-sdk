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
