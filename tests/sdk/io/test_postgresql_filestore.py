"""Tests for PostgreSQLFileStore.

Contract tests (mocked asyncpg)
-------------------------------
Use an in-process asyncpg mock (via unittest.mock) to verify the FileStore
interface contract without requiring a live PostgreSQL instance. These tests
prove the *interface* behaves correctly but cannot catch real SQL bugs.

Integration tests
-----------------
Gated behind ``@pytest.mark.integration`` and a ``POSTGRES_DSN`` env var.
Run against a real PostgreSQL instance to verify actual SQL round-trips.

    POSTGRES_DSN="postgresql://user:pass@localhost:5432/test" \
        pytest tests/sdk/io/test_postgresql_filestore.py -m integration
"""

import os
import threading
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from openhands.sdk.io.postgresql import PostgreSQLFileStore


# ---------------------------------------------------------------------------
# Fixtures — contract tests (mocked)
# ---------------------------------------------------------------------------


def _make_row(**kwargs: str) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, key: kwargs[key]
    return row


@pytest.fixture()
def store(monkeypatch: pytest.MonkeyPatch) -> PostgreSQLFileStore:
    """Return a PostgreSQLFileStore backed by a mocked asyncpg pool."""
    rows: dict[tuple[str, str], str] = {}  # (namespace, path) -> content
    held_locks: set[int] = set()  # advisory lock keys currently held

    # ------------------------------------------------------------------ #
    # Pool-level operations (CRUD)
    # ------------------------------------------------------------------ #

    async def execute(query: str, *args: Any) -> None:
        if "INSERT INTO" in query and "ON CONFLICT" in query:
            ns, path, content = args[0], args[1], args[2]
            rows[(ns, path)] = content
        elif "DELETE FROM" in query:
            ns, path, prefix = args[0], args[1], args[2]
            to_delete = [
                k
                for k in rows
                if k[0] == ns and (k[1] == path or k[1].startswith(prefix[:-1]))
            ]
            for k in to_delete:
                del rows[k]

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

    mock_pool = MagicMock()
    mock_pool.execute = execute
    mock_pool.fetchrow = fetchrow
    mock_pool.fetch = fetch
    mock_pool.close = AsyncMock()

    # ------------------------------------------------------------------ #
    # Advisory lock operations (dedicated connection via asyncpg.connect)
    # ------------------------------------------------------------------ #

    async def conn_fetchval(query: str, *args: Any) -> bool:
        if "pg_try_advisory_lock" in query:
            key = args[0]
            if key in held_locks:
                return False
            held_locks.add(key)
            return True
        return False

    async def conn_execute(query: str, *args: Any) -> None:
        if "pg_advisory_unlock" in query:
            held_locks.discard(args[0])

    async def mock_connect(dsn: str, **kwargs: Any) -> AsyncMock:
        conn = AsyncMock()
        conn.fetchval = conn_fetchval
        conn.execute = conn_execute
        conn.close = AsyncMock()
        return conn

    monkeypatch.setattr(asyncpg, "connect", mock_connect)

    # ------------------------------------------------------------------ #
    # Bypass _setup (pool creation + schema)
    # ------------------------------------------------------------------ #

    async def mock_setup(
        self: PostgreSQLFileStore, dsn: str, auto_create_schema: bool
    ) -> MagicMock:
        return mock_pool

    with patch.object(PostgreSQLFileStore, "_setup", mock_setup):
        s = PostgreSQLFileStore(dsn="postgresql://test/db", namespace="conv-123")
    return s


# ---------------------------------------------------------------------------
# Contract tests: FileStore interface
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
# Contract tests: constructor validation
# ---------------------------------------------------------------------------


def test_invalid_table_name_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid table name"):
        PostgreSQLFileStore(
            dsn="postgresql://test/db",
            namespace="ns",
            table="x; DROP TABLE users; --",
        )


def test_context_manager(store: PostgreSQLFileStore) -> None:
    # Verify __enter__/__exit__ work (close is called on exit)
    with patch.object(store, "close") as mock_close:
        with store:
            pass
        mock_close.assert_called_once()


# ---------------------------------------------------------------------------
# Contract tests: pg_advisory_lock
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


# ---------------------------------------------------------------------------
# Integration tests — require a live PostgreSQL instance
# ---------------------------------------------------------------------------

_POSTGRES_DSN = os.environ.get("POSTGRES_DSN")

_skip_no_pg = pytest.mark.skipif(
    _POSTGRES_DSN is None,
    reason="POSTGRES_DSN env var not set — skipping integration tests",
)


@pytest.fixture()
def pg_store() -> PostgreSQLFileStore:
    """Create a PostgreSQLFileStore against a real PostgreSQL instance.

    Uses a unique namespace per test run to avoid collisions.
    """
    assert _POSTGRES_DSN is not None
    namespace = f"test-{uuid.uuid4().hex[:12]}"
    store = PostgreSQLFileStore(
        dsn=_POSTGRES_DSN, namespace=namespace, auto_create_schema=True
    )
    yield store  # type: ignore[misc]
    # Cleanup: delete all rows for this namespace, then close
    store._run(
        store._pool.execute(
            f"DELETE FROM {store._table} WHERE namespace = $1",
            store._namespace,
        )
    )
    store.close()


@_skip_no_pg
@pytest.mark.integration
class TestPostgreSQLIntegration:
    """Round-trip tests against a real PostgreSQL instance."""

    def test_write_read_roundtrip(self, pg_store: PostgreSQLFileStore) -> None:
        pg_store.write("events/e0.json", '{"turn": 1}')
        assert pg_store.read("events/e0.json") == '{"turn": 1}'

    def test_overwrite(self, pg_store: PostgreSQLFileStore) -> None:
        pg_store.write("f.txt", "v1")
        pg_store.write("f.txt", "v2")
        assert pg_store.read("f.txt") == "v2"

    def test_list_and_delete(self, pg_store: PostgreSQLFileStore) -> None:
        pg_store.write("dir/a", "1")
        pg_store.write("dir/b", "2")
        pg_store.write("other/c", "3")

        listed = pg_store.list("dir")
        assert "dir/a" in listed
        assert "dir/b" in listed
        assert "other/c" not in listed

        pg_store.delete("dir")
        assert not pg_store.exists("dir/a")
        assert not pg_store.exists("dir/b")
        assert pg_store.exists("other/c")

    def test_exists(self, pg_store: PostgreSQLFileStore) -> None:
        assert not pg_store.exists("nope")
        pg_store.write("nope", "yes")
        assert pg_store.exists("nope")

    def test_read_missing(self, pg_store: PostgreSQLFileStore) -> None:
        with pytest.raises(FileNotFoundError):
            pg_store.read("does-not-exist")

    def test_lock_roundtrip(self, pg_store: PostgreSQLFileStore) -> None:
        with pg_store.lock("test-lock", timeout=5.0):
            pg_store.write("locked-file", "safe")
        assert pg_store.read("locked-file") == "safe"
