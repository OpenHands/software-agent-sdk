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
        """Best-effort: extract PIDs of stdio MCP server subprocesses.

        fastmcp's ``StdioTransport`` spawns the subprocess inside its
        ``_connect_task`` via ``stdio_client()``, and the process object is
        not exposed on the transport.  When ``sync_close()`` times out waiting
        for the async close to unwind, the ``AsyncExitStack`` never runs its
        ``finally`` block — so ``stdio_client``'s ``_terminate_process_tree()``
        never fires and the subprocess leaks.

        We recover the PID by inspecting the transport's ``_connect_task``:
        the task's coroutine frame locals include the ``AsyncExitStack`` and
        the ``stdio_client`` context, whose ``finally`` holds the ``process``.
        If that fails, we fall back to scanning child processes of this
        process for the transport's command string.
        """
        pids: list[int] = []
        try:
            transport = getattr(self, "transport", None)
            if transport is None:
                return pids
            # StdioTransport stores command/args — use them to find children
            command = getattr(transport, "command", None)
            args = getattr(transport, "args", None) or []
            if command is None:
                return pids
            # Scan /proc for child processes matching this command
            cmdline_frag = command
            if args:
                cmdline_frag = f"{command} {' '.join(str(a) for a in args[:2])}"
            self._scan_child_procs(pids, cmdline_frag, command)
        except Exception:
            logger.debug("Failed to extract transport subprocess PIDs", exc_info=True)
        return pids

    def _scan_child_procs(self, pids: list[int], fragment: str, command: str) -> None:
        """Scan /proc for descendant processes matching the transport command."""
        try:
            own_pid = os.getpid()
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                pid = int(entry)
                if pid == own_pid:
                    continue
                # Check if this is a descendant of our process
                if not self._is_descendant(pid, own_pid):
                    continue
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as f:
                        cmdline = (
                            f.read()
                            .replace(b"\x00", b" ")
                            .decode("utf-8", errors="replace")
                        )
                except (FileNotFoundError, ProcessLookupError, PermissionError):
                    continue
                if command in cmdline or fragment in cmdline:
                    pids.append(pid)
        except Exception:
            pass

    def _is_descendant(self, pid: int, ancestor: int) -> bool:
        """Check if ``pid`` is a descendant of ``ancestor`` via /proc/PPid."""
        seen: set[int] = set()
        current = pid
        while current and current not in seen:
            seen.add(current)
            try:
                with open(f"/proc/{current}/status") as f:
                    for line in f:
                        if line.startswith("PPid:"):
                            ppid = int(line.split()[1])
                            if ppid == ancestor:
                                return True
                            current = ppid
                            break
                    else:
                        break
            except (FileNotFoundError, ProcessLookupError, ValueError):
                break
        return False

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

    def _force_kill_subprocesses(self) -> None:
        """Kill any stdio MCP subprocesses that survived the async close.

        This is the safety net: if ``close()`` timed out or the portal
        thread was abandoned (see PR #4548 / issue #4598), the transport's
        ``_terminate_process_tree()`` never ran.  We kill by PID directly.
        """
        pids = self._get_transport_subprocess_pids()
        if not pids:
            return
        logger.debug(
            "MCPClient: force-killing %d leaked subprocess(es) after async close: %s",
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

        # Best-effort: try async close if parent provides it
        if hasattr(self, "close") and inspect.iscoroutinefunction(self.close):
            try:
                self._executor.run_async(self.close, timeout=10.0)
            except Exception:
                pass  # Ignore close errors during cleanup

        # Safety net: kill any subprocesses that the async close didn't
        # clean up (e.g. if the portal thread was abandoned after timeout).
        self._force_kill_subprocesses()

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
