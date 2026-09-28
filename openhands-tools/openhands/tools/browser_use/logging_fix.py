"""The browser_use server reconfigures logging for ALL loggers on import,
overwriting any custom configuration we may have applied.

We have submitted a patch which should allow us to circumvent this problematic
behavior: https://github.com/browser-use/browser-use/pull/3717

In the meantime, using this script rather than a direct import means that
logging will still work in the agent server."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import mcp.types
from mcp.server.lowlevel import Server

from openhands.sdk.utils.deprecation import warn_cleanup


def _install_mcp_legacy_handlers() -> None:
    if hasattr(Server, "list_tools"):
        return

    def list_handler(method: str, params_type: type, result_type: type, field: str):
        def register(server: Server):
            def decorator(func: Callable[[], Awaitable[list[Any]]]):
                async def handler(_context: Any, _params: Any):
                    return result_type(**{field: await func()})

                server.add_request_handler(method, params_type, handler)
                return func

            return decorator

        return register

    async def error_result(message: str) -> mcp.types.CallToolResult:
        return mcp.types.CallToolResult(
            content=[mcp.types.TextContent(type="text", text=message)],
            is_error=True,
        )

    def call_tool(server: Server):
        def decorator(
            func: Callable[[str, dict[str, Any] | None], Awaitable[list[Any]]],
        ):
            async def handler(_context: Any, params: mcp.types.CallToolRequestParams):
                try:
                    result = await func(params.name, params.arguments)
                    if isinstance(result, mcp.types.CallToolResult):
                        return result
                    return mcp.types.CallToolResult(content=list(result))
                except Exception as exc:
                    return await error_result(str(exc))

            server.add_request_handler(
                "tools/call", mcp.types.CallToolRequestParams, handler
            )
            return func

        return decorator

    setattr(
        Server,
        "list_tools",
        list_handler(
            "tools/list",
            mcp.types.PaginatedRequestParams,
            mcp.types.ListToolsResult,
            "tools",
        ),
    )
    setattr(
        Server,
        "list_resources",
        list_handler(
            "resources/list",
            mcp.types.PaginatedRequestParams,
            mcp.types.ListResourcesResult,
            "resources",
        ),
    )
    setattr(
        Server,
        "list_prompts",
        list_handler(
            "prompts/list",
            mcp.types.PaginatedRequestParams,
            mcp.types.ListPromptsResult,
            "prompts",
        ),
    )
    setattr(Server, "call_tool", call_tool)


_install_mcp_legacy_handlers()


warn_cleanup(
    "Monkey patching to prevent browser_use logging interference",
    cleanup_by="2.0.0",
    details=(
        "This workaround should be removed once browser_use fixes the "
        "problematic logging configuration code. The upstream PR #3717 "
        "(https://github.com/browser-use/browser-use/pull/3717) was closed "
        "without merge. As of browser_use 0.13.3, the server still calls "
        "logging.basicConfig(), logging.disable() and "
        "_ensure_all_loggers_use_stderr() during import and initialization, "
        "and provides no opt-out env var. Re-evaluate when browser_use "
        "changes that behavior."
    ),
)


def _noop(*args, **kwargs):
    """No-op replacement for functions"""


@dataclass
class _MockManager:
    loggerDict: dict[str, logging.Logger] = field(default_factory=dict)


@dataclass
class _MockRoot:
    handlers: list[logging.Handler] = field(default_factory=list)
    manager: _MockManager = field(default_factory=_MockManager)

    def __getattr__(self, name: str):
        return _noop


# Monkey patch before import
_orig_disable = logging.disable
_orig_basic_config = logging.basicConfig
_orig_root = logging.root
logging.disable = _noop
logging.basicConfig = _noop
logging.root = _MockRoot()
try:
    from browser_use.mcp import server  # noqa: E402
finally:
    # Restore logging after import
    logging.disable = _orig_disable
    logging.basicConfig = _orig_basic_config
    logging.root = _orig_root


# This gets called on each init - so make sure it's a noop
server._ensure_all_loggers_use_stderr = _noop

LogSafeBrowserUseServer = server.BrowserUseServer
