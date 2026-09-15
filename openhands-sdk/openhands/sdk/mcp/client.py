"""Minimal sync helpers on top of fastmcp.Client, preserving original behavior."""

import asyncio
import inspect
import threading
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any

import anyio
from fastmcp import Client as AsyncMCPClient

from openhands.sdk.mcp.exceptions import MCPError
from openhands.sdk.utils.async_executor import AsyncExecutor


if TYPE_CHECKING:
    from openhands.sdk.mcp.tool import MCPToolDefinition


ToolsReconciledCallback = Callable[
    ["MCPClient", Sequence["MCPToolDefinition"]],
    None,
]


class MCPClient(AsyncMCPClient):
    """MCP client with sync helpers and lifecycle management.

    Extends fastmcp.Client with:
      - call_async_from_sync(awaitable_or_fn, *args, timeout=None, **kwargs)
      - call_sync_from_async(fn, *args, **kwargs)  # await this from async code

    After create_mcp_tools() populates it, use as a sync context manager:

        with create_mcp_tools(config) as client:
            for tool in client.tools:
                # use tool
        # Connection automatically closed

    Or manage lifecycle manually by calling sync_close() when done.
    """

    _executor: AsyncExecutor
    _closed: bool
    _tools: "list[MCPToolDefinition]"
    _tools_reconciled_callback: ToolsReconciledCallback | None
    _connection_future: Future[None] | None = None
    _connection_stop: anyio.Event | None = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._executor = AsyncExecutor()
        self._closed = False
        self._tools = []
        self._tools_reconciled_callback = None
        self._connection_future = None
        self._connection_stop = None

    @property
    def tools(self) -> "list[MCPToolDefinition]":
        """The MCP tools using this client connection (returns a copy)."""
        return list(self._tools)

    async def connect(self) -> None:
        """Establish connection to the MCP server."""
        try:
            await self.__aenter__()
        except RuntimeError as exc:
            raise MCPError("MCP Connection Failure") from exc

    async def _own_connection(
        self, ready: threading.Event, errors: list[BaseException]
    ) -> None:
        try:
            await self.connect()
            self._connection_stop = anyio.Event()
        except BaseException as exc:
            errors.append(exc)
            raise
        finally:
            ready.set()

        assert self._connection_stop is not None
        try:
            await self._connection_stop.wait()
        finally:
            await self.__aexit__(None, None, None)

    def sync_connect(self, timeout: float) -> None:
        """Keep FastMCP's background session alive on the portal loop."""
        if self._connection_future is not None:
            return

        ready = threading.Event()
        errors: list[BaseException] = []
        future = self._executor.portal.start_task_soon(
            self._own_connection, ready, errors
        )
        self._connection_future = future
        if not ready.wait(timeout):
            future.cancel()
            raise TimeoutError("MCP connection timed out")
        if errors:
            future.result()

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

        future = self._connection_future
        stop = self._connection_stop
        if future is not None and stop is not None:
            try:
                self._executor.portal.call(stop.set)
                future.result(timeout=10.0)
            except Exception:
                future.cancel()
        elif hasattr(self, "close") and inspect.iscoroutinefunction(self.close):
            try:
                self._executor.run_async(self.close, timeout=10.0)
            except Exception:
                pass

        # Always cleanup the executor
        self._executor.close()
        self._closed = True

    def __del__(self):
        """Cleanup on deletion."""
        try:
            self.sync_close()
        except Exception:
            pass  # Ignore cleanup errors during deletion

    # Sync context manager support
    def __enter__(self) -> "MCPClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.sync_close()

    # Iteration support for tools
    def __iter__(self) -> "Iterator[MCPToolDefinition]":
        return iter(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def __getitem__(self, index: int) -> "MCPToolDefinition":
        return self._tools[index]
