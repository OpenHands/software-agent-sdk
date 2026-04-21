"""Tests for PostgreSQLFileStore.

Uses an in-process asyncpg mock (via unittest.mock) to avoid requiring a live
PostgreSQL instance. Tests cover the FileStore interface contract and the
per-path threading lock behaviour.
"""

import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openhands.sdk.io.postgresql import PostgreSQLFileStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_row(**kwargs: str) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, key: kwargs[key]
    return row


@pytest.fixture()
def store(monkeypatch: pytest.MonkeyPatch) -> PostgreSQLFileStore:
    """Return a PostgreSQLFileStore backed by a mocked asyncpg pool."""
    rows: dict[tuple[str, str], str] = {}  # (namespace, path) -> content

    async def execute(query: str, *args: Any) -> None:
        # INSERT ... ON CONFLICT DO UPDATE
        if "INSERT INTO" in query and "ON CONFLICT" in query:
            ns, path, content = args[0], args[1], args[2]
            rows[(ns, path)] = content
        # DELETE
        elif "DELETE FROM" in query:
            ns, path, prefix = args[0], args[1], args[2]
            to_delete = [
                k
                for k in rows
                if k[0] == ns and (k[1] == path or k[1].startswith(prefix[:-1]))
            ]
            for k in to_delete:
                del rows[k]
        # CREATE TABLE / INDEX — no-op
        pass

    async def fetchrow(query: str, *args: Any) -> MagicMock | None:
        ns, path = args[0], args[1]
        content = rows.get((ns, path))
        if content is None:
            return None
        return _make_row(content=content)

    async def fetch(query: str, *args: Any) -> list[MagicMock]:
        ns, path, prefix = args[0], args[1], args[2]
        matched = [
            k
            for k in rows
            if k[0] == ns and (k[1] == path or k[1].startswith(prefix[:-1]))
        ]
        return [_make_row(path=k[1]) for k in sorted(matched)]

    @asynccontextmanager
    async def acquire() -> AsyncIterator[AsyncMock]:
        conn = AsyncMock()
        conn.execute = execute
        yield conn

    mock_pool = MagicMock()
    mock_pool.execute = execute
    mock_pool.fetchrow = fetchrow
    mock_pool.fetch = fetch
    mock_pool.acquire = acquire
    mock_pool.close = AsyncMock()

    async def mock_setup(self: PostgreSQLFileStore, dsn: str) -> MagicMock:
        return mock_pool

    with patch.object(PostgreSQLFileStore, "_setup", mock_setup):
        s = PostgreSQLFileStore(dsn="postgresql://test/db", namespace="conv-123")
    return s


# ---------------------------------------------------------------------------
# Tests: FileStore interface contract
# ---------------------------------------------------------------------------


def test_write_and_read(store: PostgreSQLFileStore) -> None:
    store.write("events/e0.json", '{"id": "abc"}')
    assert store.read("events/e0.json") == '{"id": "abc"}'


def test_write_bytes(store: PostgreSQLFileStore) -> None:
    store.write("events/e1.json", b'{"id": "bytes"}')
    assert store.read("events/e1.json") == '{"id": "bytes"}'


def test_overwrite(store: PostgreSQLFileStore) -> None:
    store.write("file.txt", "first")
    store.write("file.txt", "second")
    assert store.read("file.txt") == "second"


def test_read_missing_raises(store: PostgreSQLFileStore) -> None:
    with pytest.raises(FileNotFoundError):
        store.read("nonexistent")


def test_exists(store: PostgreSQLFileStore) -> None:
    assert not store.exists("x")
    store.write("x", "v")
    assert store.exists("x")


def test_list(store: PostgreSQLFileStore) -> None:
    store.write("events/e0.json", "a")
    store.write("events/e1.json", "b")
    store.write("other/file", "c")
    result = store.list("events")
    assert "events/e0.json" in result
    assert "events/e1.json" in result
    assert "other/file" not in result


def test_delete(store: PostgreSQLFileStore) -> None:
    store.write("events/e0.json", "a")
    store.write("events/e1.json", "b")
    store.delete("events")
    assert not store.exists("events/e0.json")
    assert not store.exists("events/e1.json")


def test_get_absolute_path(store: PostgreSQLFileStore) -> None:
    path = store.get_absolute_path("events/e0.json")
    assert "conv-123" in path
    assert "events/e0.json" in path


# ---------------------------------------------------------------------------
# Tests: lock
# ---------------------------------------------------------------------------


def test_lock_serialises_access(store: PostgreSQLFileStore) -> None:
    results: list[int] = []

    def worker(n: int) -> None:
        with store.lock(".lock"):
            results.append(n)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == list(range(5))


def test_lock_timeout(store: PostgreSQLFileStore) -> None:
    ready = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with store.lock(".lock"):
            ready.set()
            release.wait()

    t = threading.Thread(target=holder)
    t.start()
    ready.wait()

    with pytest.raises(TimeoutError):
        with store.lock(".lock", timeout=0.05):
            pass

    release.set()
    t.join()
