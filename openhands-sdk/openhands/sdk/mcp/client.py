"""Minimal sync helpers on top of fastmcp.Client, preserving original behavior."""

import asyncio
import inspect
import os
import signal
import time
from collections.abc import Callable, Iterator, Sequence
from typing import TYPE_CHECKING, Any

from fastmcp import Client as AsyncMCPClient

from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.exceptions import MCPError
from openhands.sdk.utils.async_executor import AsyncExecutor


if TYPE_CHECKING:
    from openhands.sdk.mcp.tool import MCPToolDefinition


logger = get_logger(__name__)


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._executor = AsyncExecutor()
        self._closed = False
        self._tools = []
        self._tools_reconciled_callback = None

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

    def _get_transport_subprocess_pids(self) -> list[int]:
        """Extract PIDs owned by active FastMCP stdio transports.

        FastMCP retains each stdio context manager on the connection task's
        ``AsyncExitStack``. Its async-generator frame owns the exact process
        object, so this avoids matching another client's subprocess by command.
        """
        transport = getattr(self, "transport", None)
        if transport is None:
            return []

        pids: list[int] = []
        pending = [transport]
        seen: set[int] = set()

        while pending:
            transport = pending.pop()
            if id(transport) in seen:
                continue
            seen.add(id(transport))

            child = getattr(transport, "transport", None)
            if child is not None:
                pending.append(child)
            pending.extend(getattr(transport, "_transports", ()))

            task = getattr(transport, "_connect_task", None)
            coroutine = task.get_coro() if task is not None else None
            frame = getattr(coroutine, "cr_frame", None)
            stack = frame.f_locals.get("stack") if frame is not None else None
            for _, callback in getattr(stack, "_exit_callbacks", ()):
                manager = getattr(callback, "__self__", None)
                generator = getattr(manager, "gen", None)
                generator_frame = getattr(generator, "ag_frame", None)
                process = (
                    generator_frame.f_locals.get("process")
                    if generator_frame is not None
                    else None
                )
                pid = getattr(process, "pid", None)
                if isinstance(pid, int):
                    pids.append(pid)

        return list(dict.fromkeys(pids))

    def _kill_process_group(self, pid: int) -> None:
        """Kill a process and its group (SIGTERM then SIGKILL)."""
        try:
            os.kill(pid, 0)  # Check if alive
        except (ProcessLookupError, PermissionError, OSError):
            return  # Already dead or not ours

        # Try killing the process group first (handles `npm exec` → `node`)
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    os.kill(pid, sig)
                except (ProcessLookupError, PermissionError, OSError):
                    return  # Gone or not ours
            if sig == signal.SIGTERM:
                time.sleep(0.5)  # Give it time to exit gracefully
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, OSError):
                return  # Dead

    def _force_kill_subprocesses(self, pids: Sequence[int]) -> None:
        """Kill stdio MCP subprocesses captured before async close."""
        if not pids:
            return
        logger.debug(
            "MCPClient: force-killing %d stdio subprocess(es) after async close: %s",
            len(pids),
            pids,
        )
        for pid in pids:
            self._kill_process_group(pid)

    def sync_close(self) -> None:
        """
        Synchronously close the MCP client and cleanup resources.

        This will attempt to call the async close() method if available,
        then shutdown the background event loop. Safe to call multiple times.

        As a safety net, any stdio MCP subprocesses that survived the async
        close (due to timeout or portal-thread abandonment) are killed
        unconditionally before the executor is shut down.  See issue #4598.
        """
        if self._closed:
            return

        subprocess_pids = self._get_transport_subprocess_pids()

        # Best-effort: try async close if parent provides it
        if hasattr(self, "close") and inspect.iscoroutinefunction(self.close):
            try:
                self._executor.run_async(self.close, timeout=10.0)
            except Exception:
                pass  # Ignore close errors during cleanup

        # Kill the exact processes owned by this client's stdio transports,
        # including any that survived an abandoned portal thread.
        self._force_kill_subprocesses(subprocess_pids)

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
