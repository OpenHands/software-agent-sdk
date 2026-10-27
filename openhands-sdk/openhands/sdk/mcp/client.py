"""Minimal sync helpers on top of fastmcp.Client, preserving original behavior."""

import asyncio
import inspect
from collections.abc import Callable, Iterator, Sequence
from typing import TYPE_CHECKING, Any

from fastmcp import Client as AsyncMCPClient

from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.exceptions import MCPConnectionError, MCPError
from openhands.sdk.utils.async_executor import AsyncExecutor


logger = get_logger(__name__)


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

    def _extract_connection_target(
        self,
    ) -> tuple[str | None, str | None, dict[str, Any] | None]:
        """Extract server name, URL/command target, and config dict if available."""
        transport = self.transport if hasattr(self, "transport") else None
        config_dict: dict[str, Any] | None = None
        if transport is not None:
            config = transport.config if hasattr(transport, "config") else None
            if config is not None and hasattr(config, "model_dump"):
                try:
                    dumped = config.model_dump()
                    if isinstance(dumped, dict):
                        config_dict = dumped
                except Exception:
                    pass
            if isinstance(config_dict, dict):
                mcp_servers = config_dict.get("mcpServers")
                if isinstance(mcp_servers, dict) and mcp_servers:
                    name, srv = next(iter(mcp_servers.items()))
                    if isinstance(srv, dict):
                        url = srv.get("url")
                        if url:
                            return name, str(url), config_dict
                        cmd = srv.get("command")
                        if cmd:
                            args = srv.get("args")
                            args_str = f" {' '.join(args)}" if args else ""
                            return name, f"{cmd}{args_str}", config_dict
                    return name, None, config_dict
            if hasattr(transport, "url") and transport.url:  # type: ignore[union-attr]
                return None, str(transport.url), config_dict  # type: ignore[union-attr]
        return None, None, config_dict

    @staticmethod
    def _troubleshooting_guidance() -> str:
        return (
            "Possible solutions:\n"
            "  1. Check if the MCP server is running and accepting connections\n"
            "  2. Verify network connectivity, firewall rules, and port configuration\n"
            "  3. Verify the server URL or command and authentication credentials"
        )

    async def connect(self) -> None:
        """Establish connection to the MCP server."""
        async with self._session_state.lock:
            session_task = self._session_state.session_task
            if session_task is not None and session_task.done():
                self._reset_session_state(full=True)
        try:
            await self.__aenter__()
        except MCPError:
            raise
        except Exception as exc:
            server_name, target, config_dict = self._extract_connection_target()
            cause = exc.__cause__ or exc
            guidance = self._troubleshooting_guidance()
            msg = (
                "Failed to connect to MCP server"
                + (f" '{server_name}'" if server_name else "")
                + (f" at '{target}'" if target else "")
                + f": {cause}\n\n"
                + f"{guidance}\n"
            )
            raise MCPConnectionError(
                msg, server_name=server_name, url=target, config=config_dict
            ) from exc

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

    async def close(self) -> None:
        """Close the MCP client and cleanup resources safely."""
        # If the background session task already completed with an exception,
        # fastmcp._disconnect() would await that failed task and re-raise the
        # exception during cleanup. Clearing the failed task avoids mutating or
        # chaining teardown exceptions onto the original error.
        if hasattr(self, "_session_state"):
            task = self._session_state.session_task
            if task is not None and task.done() and not task.cancelled():
                try:
                    if task.exception() is not None:
                        self._session_state.session_task = None
                except Exception:
                    pass
        try:
            await super().close()
        except Exception as exc:
            logger.debug("Error closing MCP client: %s", exc)

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
