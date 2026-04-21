"""PostgreSQL-backed FileStore for SDK EventLog persistence.

Enables full conversation resumption — including tool_call/tool_result events —
by persisting the SDK EventLog to a PostgreSQL database.

Requirements:
    pip install asyncpg

Example::

    from openhands.sdk.io.postgresql import PostgreSQLFileStore
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation

    store = PostgreSQLFileStore(
        dsn="postgresql://user:pass@host:5432/db",
        namespace=str(conversation_id),
    )
    conv = LocalConversation(agent=agent, workspace=workspace, file_store=store)
"""

import asyncio
import concurrent.futures
import hashlib
import threading
from collections.abc import Coroutine, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

import asyncpg

from .base import FileStore


_T = TypeVar("_T")


__all__ = ["PostgreSQLFileStore"]

DEFAULT_TABLE = "sdk_filestore"


class PostgreSQLFileStore(FileStore):
    """SDK FileStore backed by a PostgreSQL table.

    Each instance is namespaced (typically by conversation ID) so multiple
    conversations can share a single table without path collisions.

    All synchronous FileStore methods bridge to asyncpg via a dedicated
    background event loop thread, avoiding conflicts with the host
    application's asyncio loop.

    Args:
        dsn: asyncpg-compatible DSN, e.g.
             ``postgresql://user:pass@host:5432/dbname``
        namespace: Logical scope for all path operations.
                   Use a unique conversation ID to isolate event logs.
        table: Table name (default: ``sdk_filestore``).
               The table and index are created on first instantiation.

    Note:
        ``lock()`` uses PostgreSQL session-level advisory locks
        (``pg_try_advisory_lock``), which are safe for concurrent writers
        across multiple processes and instances.
    """

    def __init__(self, dsn: str, namespace: str, table: str = DEFAULT_TABLE) -> None:
        self._dsn = dsn
        self._namespace = namespace
        self._table = table

        # Dedicated event loop in a daemon background thread.
        # run_coroutine_threadsafe() is safe to call from any thread,
        # including run_in_executor workers used by the SDK.
        self._loop = asyncio.new_event_loop()
        self._bg_thread = threading.Thread(
            target=self._loop.run_forever,
            daemon=True,
            name=f"pg-filestore-{namespace[:8]}",
        )
        self._bg_thread.start()

        # Initialize pool and schema in one async call to avoid create_pool
        # type-stub limitations (asyncpg.create_pool is not typed as a coroutine).
        future: concurrent.futures.Future[asyncpg.Pool] = (
            asyncio.run_coroutine_threadsafe(self._setup(dsn), self._loop)
        )
        self._pool: asyncpg.Pool = future.result()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    async def _setup(self, dsn: str) -> asyncpg.Pool:
        pool = await asyncpg.create_pool(dsn)
        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._table} (
                    namespace  TEXT        NOT NULL,
                    path       TEXT        NOT NULL,
                    content    TEXT        NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (namespace, path)
                )
                """
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS {self._table}_ns_idx
                ON {self._table} (namespace)
                """
            )
        return pool

    def _run(self, coro: Coroutine[Any, Any, _T]) -> _T:
        """Submit coroutine to the background loop and block for the result."""
        future: concurrent.futures.Future[_T] = asyncio.run_coroutine_threadsafe(
            coro, self._loop
        )
        return future.result()

    def _advisory_key(self, path: str) -> int:
        """Derive a 64-bit signed advisory lock key from namespace + path."""
        raw = f"{self._namespace}\x00{path}".encode()
        digest = hashlib.sha256(raw).digest()
        return int.from_bytes(digest[:8], "big", signed=True)

    async def _lock_acquire(
        self, key: int, path: str, timeout: float
    ) -> asyncpg.Connection:
        """Acquire a session-level advisory lock on a dedicated connection."""
        conn: asyncpg.Connection = await asyncpg.connect(self._dsn)
        deadline = self._loop.time() + timeout
        while True:
            acquired = bool(await conn.fetchval("SELECT pg_try_advisory_lock($1)", key))
            if acquired:
                return conn
            remaining = deadline - self._loop.time()
            if remaining <= 0:
                await conn.close()
                raise TimeoutError(
                    f"Could not acquire advisory lock for {path!r} within {timeout}s"
                )
            await asyncio.sleep(min(0.05, remaining))

    async def _lock_release(self, conn: asyncpg.Connection, key: int) -> None:
        """Release the advisory lock and close the dedicated connection."""
        await conn.execute("SELECT pg_advisory_unlock($1)", key)
        await conn.close()

    # ------------------------------------------------------------------ #
    # FileStore interface
    # ------------------------------------------------------------------ #

    def write(self, path: str, contents: str | bytes) -> None:
        if isinstance(contents, bytes):
            contents = contents.decode("utf-8")
        self._run(
            self._pool.execute(
                f"""
                INSERT INTO {self._table} (namespace, path, content, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (namespace, path) DO UPDATE
                  SET content    = EXCLUDED.content,
                      updated_at = NOW()
                """,
                self._namespace,
                path,
                contents,
            )
        )

    def read(self, path: str) -> str:
        row = self._run(
            self._pool.fetchrow(
                f"SELECT content FROM {self._table} WHERE namespace = $1 AND path = $2",
                self._namespace,
                path,
            )
        )
        if row is None:
            raise FileNotFoundError(path)
        return str(row["content"])

    def list(self, path: str) -> list[str]:
        prefix = path.rstrip("/") + "/%"
        rows = self._run(
            self._pool.fetch(
                f"""
                SELECT path FROM {self._table}
                WHERE namespace = $1
                  AND (path = $2 OR path LIKE $3)
                ORDER BY path
                """,
                self._namespace,
                path,
                prefix,
            )
        )
        return [str(r["path"]) for r in rows]

    def delete(self, path: str) -> None:
        prefix = path.rstrip("/") + "/%"
        self._run(
            self._pool.execute(
                f"""
                DELETE FROM {self._table}
                WHERE namespace = $1
                  AND (path = $2 OR path LIKE $3)
                """,
                self._namespace,
                path,
                prefix,
            )
        )

    def exists(self, path: str) -> bool:
        row = self._run(
            self._pool.fetchrow(
                f"SELECT 1 FROM {self._table} WHERE namespace = $1 AND path = $2",
                self._namespace,
                path,
            )
        )
        return row is not None

    def get_absolute_path(self, path: str) -> str:
        return f"postgresql://{self._table}/{self._namespace}/{path}"

    @contextmanager
    def lock(self, path: str, timeout: float = 30.0) -> Iterator[None]:
        """Acquire a PostgreSQL session-level advisory lock for the given path.

        Safe for concurrent writers across multiple processes and instances.
        Uses pg_try_advisory_lock with 50 ms polling until timeout.
        The lock key is derived from namespace + path via SHA-256.
        """
        key = self._advisory_key(path)
        conn = self._run(self._lock_acquire(key, path, timeout))
        try:
            yield
        finally:
            self._run(self._lock_release(conn, key))

    def close(self) -> None:
        """Close the connection pool and stop the background event loop."""
        self._run(self._pool.close())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._bg_thread.join(timeout=5)
