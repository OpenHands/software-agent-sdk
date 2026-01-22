"""Minimal sync helpers on top of fastmcp.Client, preserving original behavior."""

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from fastmcp import Client as AsyncMCPClient

from openhands.sdk.utils.async_executor import AsyncExecutor


class MCPClient(AsyncMCPClient):
    """
    Behaves exactly like fastmcp.Client (same constructor & async API),
    but owns a background event loop and offers:
      - call_async_from_sync(awaitable_or_fn, *args, timeout=None, **kwargs)
      - call_sync_from_async(fn, *args, **kwargs)  # await this from async code
      - connect() for explicit connection establishment
      - sync_close() for synchronous cleanup
    """

    _executor: AsyncExecutor
    _closed: bool

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._executor = AsyncExecutor()
        self._closed = False

    async def connect(self) -> None:
        """Establish connection to the MCP server.

        This is an explicit alternative to using the async context manager.
        The connection is cleaned up via sync_close() when done.
        """
        await self.__aenter__()

    def call_async_from_sync(
        self,
        awaitable_or_fn: Callable[..., Any] | Any,
        *args,
        timeout: float,
        **kwargs,
    ) -> Any:
        """
        Run a coroutine or async function on this client's loop from sync code.

        Usage:
            mcp.call_async_from_sync(async_fn, arg1, kw=...)
            mcp.call_async_from_sync(coro)
        """
        return self._executor.run_async(
            awaitable_or_fn, *args, timeout=timeout, **kwargs
        )

    async def call_sync_from_async(
        self, fn: Callable[..., Any], *args, **kwargs
    ) -> Any:
        """
        Await running a blocking function in the default threadpool from async code.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    def sync_close(self) -> None:
        """
        Synchronously close the MCP client and cleanup resources.

        This will attempt to call the async close() method if available,
        then shutdown the background event loop. Safe to call multiple times.
        """
        if self._closed:
            return

        # Best-effort: try async close if parent provides it
        if hasattr(self, "close") and inspect.iscoroutinefunction(self.close):
            try:
                self._executor.run_async(self.close, timeout=10.0)
            except Exception:
                pass  # Ignore close errors during cleanup

        # Always cleanup the executor
        self._executor.close()

        # Mark closed only after cleanup succeeds
        # (Both close methods are idempotent, so retries are safe)
        self._closed = True

    def __del__(self):
        """Cleanup on deletion."""
        try:
            self.sync_close()
        except Exception:
            pass  # Ignore cleanup errors during deletion
