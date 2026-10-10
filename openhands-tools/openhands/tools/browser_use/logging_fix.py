"""The browser_use server reconfigures logging for ALL loggers on import,
overwriting any custom configuration we may have applied.

We have submitted a patch which should allow us to circumvent this problematic
behavior: https://github.com/browser-use/browser-use/pull/3717

In the meantime, using this script rather than a direct import means that
logging will still work in the agent server."""

import logging
from dataclasses import dataclass, field

from openhands.sdk.utils.deprecation import warn_cleanup


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

warn_cleanup(
    "Shim mcp 1.x Server handler decorators for browser_use under mcp 2.x",
    cleanup_by="2.0.0",
    details=(
        "browser_use (<=0.13.x) pins mcp==1.26.0 and registers MCP handlers "
        "with the decorator-style API (@server.list_tools(), "
        "@server.call_tool(), ...) that mcp 2.x removed in favor of "
        "constructor-based handlers plus add_request_handler(). OpenHands "
        "never speaks MCP to this server (it calls the server's private "
        "methods directly), but BrowserUseServer.__init__ still executes the "
        "decorator registrations, so under mcp 2.x construction fails with "
        "AttributeError: 'Server' object has no attribute 'list_tools'. "
        "Remove this shim once browser_use supports mcp 2.x natively."
    ),
)


def _patch_mcp2_server_compat() -> None:
    """Re-add the mcp 1.x decorator-style handler registration under mcp 2.x.

    mcp 2.x replaced the ``@server.list_tools()`` / ``@server.call_tool()``
    decorators with constructor-based ``on_*`` handlers plus
    ``add_request_handler``. This faithfully re-implements the decorators on
    top of ``add_request_handler``. Under mcp 1.x the decorators already
    exist and this is a no-op.
    """
    try:
        from mcp.server.lowlevel.server import Server
    except Exception:
        return  # mcp not installed: nothing to shim.

    if hasattr(Server, "list_tools"):
        return  # mcp 1.x already provides the decorators; nothing to do.

    import mcp.types as types

    def _make_list_decorator(method, result_cls, field_name):
        def decorator_factory(self):
            def register(fn):
                async def handler(_ctx, _params):
                    items = await fn()
                    return result_cls(**{field_name: items})

                self.add_request_handler(method, types.PaginatedRequestParams, handler)
                return fn

            return register

        return decorator_factory

    Server.list_tools = _make_list_decorator(  # type: ignore[attr-defined]
        "tools/list", types.ListToolsResult, "tools"
    )
    Server.list_resources = _make_list_decorator(  # type: ignore[attr-defined]
        "resources/list", types.ListResourcesResult, "resources"
    )
    Server.list_prompts = _make_list_decorator(  # type: ignore[attr-defined]
        "prompts/list", types.ListPromptsResult, "prompts"
    )

    def call_tool(self):
        def register(fn):
            async def handler(_ctx, params):
                content = await fn(params.name, params.arguments)
                return types.CallToolResult(content=content)

            self.add_request_handler("tools/call", types.CallToolRequestParams, handler)
            return fn

        return register

    Server.call_tool = call_tool  # type: ignore[attr-defined]


_patch_mcp2_server_compat()


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
