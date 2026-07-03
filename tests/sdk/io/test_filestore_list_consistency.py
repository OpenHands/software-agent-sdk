"""InMemoryFileStore.list must agree with LocalFileStore.list on path boundaries.

Regression tests: the in-memory store used bare string prefix matching
(``file.startswith(path)`` + ``removeprefix``), which is not aware of the
``/`` directory boundary. Compared with ``LocalFileStore`` (documented as
S3-consistent) it diverged in two ways:

1. Listing a directory whose name is a prefix of a sibling (``a/b`` vs
   ``a/bc.txt``) invented phantom subdirectories from the sibling's keys.
2. Listing an exact file path raised ``IndexError`` instead of returning
   ``[path]``.
"""

import pytest

from openhands.sdk.io.local import LocalFileStore
from openhands.sdk.io.memory import InMemoryFileStore


FIXTURE = {
    "sessions/abc/event.json": "{}",
    "sessions/abcdef/other.json": "{}",
    "sessions/abc/nested/deep.json": "{}",
    "top.txt": "x",
}


def make_stores(tmp_path):
    memory = InMemoryFileStore()
    local = LocalFileStore(root=str(tmp_path))
    for store in (memory, local):
        for key, contents in FIXTURE.items():
            store.write(key, contents)
    return memory, local


def test_list_exact_file_returns_path_and_does_not_raise(tmp_path):
    memory, local = make_stores(tmp_path)

    assert local.list("sessions/abc/event.json") == ["sessions/abc/event.json"]
    # Used to raise IndexError on the in-memory store
    assert memory.list("sessions/abc/event.json") == ["sessions/abc/event.json"]


def test_list_sibling_prefix_produces_no_phantom_entries(tmp_path):
    memory, local = make_stores(tmp_path)

    expected = {"sessions/abc/event.json", "sessions/abc/nested/"}
    assert set(local.list("sessions/abc")) == expected
    # Used to also contain a phantom "sessions/abc/def/" derived from the
    # sibling key "sessions/abcdef/other.json"
    assert set(memory.list("sessions/abc")) == expected


@pytest.mark.parametrize(
    "path",
    ["sessions", "sessions/", "sessions/abc", "sessions/abc/nested"],
)
def test_list_directories_matches_local(tmp_path, path):
    memory, local = make_stores(tmp_path)

    assert set(memory.list(path)) == set(local.list(path))


def test_list_missing_path_is_empty(tmp_path):
    memory, local = make_stores(tmp_path)

    assert local.list("sessions/zzz") == []
    assert memory.list("sessions/zzz") == []


def test_list_root_still_works(tmp_path):
    memory, _ = make_stores(tmp_path)

    assert set(memory.list("")) == {"sessions/", "top.txt"}
