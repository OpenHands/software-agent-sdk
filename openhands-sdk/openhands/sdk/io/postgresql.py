"""PostgreSQL-backed FileStore for SDK EventLog persistence.

Enables full conversation resumption including tool_call/tool_result events
by persisting the SDK EventLog to a PostgreSQL database instead of the
local filesystem or in-memory store.

Requirements:
    pip install asyncpg  # or: pip install openhands-sdk[postgresql]

Example usage::

    from openhands.sdk.io.postgresql import PostgreSQLFileStore
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation

    store = PostgreSQLFileStore(
        dsn="postgresql://user:pass@host:5432/db",
        namespace=str(conversation_id),
    )
    conv = LocalConversation(agent=agent, workspace=workspace, file_store=store)
"""

import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator

from .base import FileStore

if TYPE_CHECKING:
    import asyncpg

__all__ = ["PostgreSQLFileStore"]

DEFAULT_TABLE = "sdk_filestore"


class PostgreSQLFileStore(FileStore):
    """SDK FileStore backed by a PostgreSQL table.

    Each instance is namespaced (typically by conversation ID) so multiple
    conversations can share a single table without path collisions.

    All synchronous FileStore methods bridge to asyncpg via a dedicated
    background event loop thread, avoiding event loop conflicts with the
    host application's asyncio loop.

    Args:
        dsn: asyncpg-compatible DSN, e.g.
             ``postgresql://user:pass@host:5432/dbname``
        namespace: Logical scope for all path operations.
                   Use a unique conversation ID to isolate event logs.
        table: Table name (default: ``sdk_filestore``).
               The table and index are created on first instantiation.

    Note:
        For multi-process deployments, replace the ``threading.Lock``-based
        ``lock()`` with PostgreSQL advisory locks (``pg_try_advisory_lock``).
    """

    def __init__(self, dsn: str, namespace: str, table: str = DEFAULT_TABLE) -> None:
        try:
            import asyncio

            import asyncpg as _asyncpg
        except ImportError as exc:
            raise ImportError(
                "asyncpg is required for PostgreSQLFileStore. "
                "Install it with: pip install asyncpg"
            ) from exc

        self._asyncpg = _asyncpg
        self._asyncio = asyncio
        self._dsn = dsn
        self._namespace = namespace
        self._table = table

        # Dedicated event loop in a daemon background thread.
        # All async operations are submitted via run_coroutine_threadsafe(),
        # which is safe to call from any thread including run_in_executor workers.
        self._loop = asyncio.new_event_loop()
        self._bg_thread = threading.Thread(
            target=self._loop.run_forever,
            daemon=True,
            name=f"pg-filestore-{namespace[:8]}",
        )
        self._bg_thread.start()

        self._pool = self._run(_asyncpg.create_pool(dsn))
        self._run(self._ensure_table())

        # Per-path threading locks for EventLog sequential index assignment.
        # Sufficient for single-process deployments.
        self._path_locks: dict[str, threading.Lock] = {}
        self._meta_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _run(self, coro):
        """Submit coroutine to the background loop and block for the result."""
        return self._asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    async def _ensure_table(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._table} (
                    namespace  TEXT        NOT NULL,
                    path       TEXT        NOT NULL,
                    content    TEXT        NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (namespace, path)
                )
            """)
            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS {self._table}_ns_idx
                ON {self._table} (namespace)
            """)

    # ------------------------------------------------------------------ #
    # FileStore interface
    # ------------------------------------------------------------------ #

    def write(self, path: str, contents: str | bytes) -> None:
        if isinstance(contents, bytes):
            contents = contents.decode("utf-8")
        self._run(self._pool.execute(
            f"""
            INSERT INTO {self._table} (namespace, path, content, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (namespace, path) DO UPDATE
              SET content    = EXCLUDED.content,
                  updated_at = NOW()
            """,
            self._namespace, path, contents,
        ))

    def read(self, path: str) -> str:
        row = self._run(self._pool.fetchrow(
            f"SELECT content FROM {self._table}"
            f" WHERE namespace = $1 AND path = $2",
            self._namespace, path,
        ))
        if row is None:
            raise FileNotFoundError(path)
        return row["content"]

    def list(self, path: str) -> list[str]:
        prefix = path.rstrip("/") + "/%"
        rows = self._run(self._pool.fetch(
            f"""
            SELECT path FROM {self._table}
            WHERE namespace = $1
              AND (path = $2 OR path LIKE $3)
            ORDER BY path
            """,
            self._namespace, path, prefix,
        ))
        return [r["path"] for r in rows]

    def delete(self, path: str) -> None:
        prefix = path.rstrip("/") + "/%"
        self._run(self._pool.execute(
            f"""
            DELETE FROM {self._table}
            WHERE namespace = $1
              AND (path = $2 OR path LIKE $3)
            """,
            self._namespace, path, prefix,
        ))

    def exists(self, path: str) -> bool:
        row = self._run(self._pool.fetchrow(
            f"SELECT 1 FROM {self._table}"
            f" WHERE namespace = $1 AND path = $2",
            self._namespace, path,
        ))
        return row is not None

    def get_absolute_path(self, path: str) -> str:
        return f"postgresql://{self._table}/{self._namespace}/{path}"

    @contextmanager
    def lock(self, path: str, timeout: float = 30.0) -> Iterator[None]:
        """Acquire an in-process threading lock for the given path.

        The EventLog calls this to serialize sequential index assignment.
        A threading.Lock per path is sufficient for single-process deployments.
        """
        with self._meta_lock:
            if path not in self._path_locks:
                self._path_locks[path] = threading.Lock()
            lock = self._path_locks[path]

        acquired = lock.acquire(timeout=timeout)
        if not acquired:
            raise TimeoutError(
                f"Could not acquire lock for '{path}' within {timeout}s"
            )
        try:
            yield
        finally:
            lock.release()

    def close(self) -> None:
        """Close the connection pool and stop the background event loop."""
        self._run(self._pool.close())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._bg_thread.join(timeout=5)
