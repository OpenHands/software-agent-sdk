"""Tests for the mcp 2.x compatibility shim in logging_fix.

browser_use (<=0.13.x) pins mcp==1.26.0 and registers MCP handlers with the
decorator-style Server API (@server.list_tools(), @server.call_tool(), ...)
that mcp 2.x removed in favor of add_request_handler(). logging_fix re-adds
those decorators on top of add_request_handler() so that BrowserUseServer can
be constructed under either mcp major version.
"""

import mcp.types as types
import pytest
from mcp.server.lowlevel.server import Server

from openhands.tools.browser_use import logging_fix


def _shim_active() -> bool:
    """True when Server.list_tools comes from the shim, not from mcp itself."""
    return getattr(Server, "list_tools", None) is not None and (
        Server.list_tools.__module__ == logging_fix.__name__
    )


def _shimmed_server() -> Server:
    if hasattr(Server, "list_tools") and not _shim_active():
        pytest.skip("mcp 1.x environment: decorators exist natively, shim is a no-op")
    logging_fix._patch_mcp2_server_compat()
    assert _shim_active()
    return Server("test")


def test_shim_is_noop_when_mcp_decorators_exist():
    """On mcp 1.x the native decorators must not be replaced."""
    if not hasattr(Server, "list_tools") or _shim_active():
        pytest.skip("requires an mcp 1.x environment")
    native = Server.list_tools
    logging_fix._patch_mcp2_server_compat()
    assert Server.list_tools is native


async def test_list_tools_decorator_registers_handler():
    server = _shimmed_server()

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return [types.Tool(name="echo", inputSchema={"type": "object"})]

    handlers = server._request_handlers  # type: ignore[attr-defined]
    entry = handlers.get("tools/list")
    assert entry is not None
    result = await entry.handler(None, types.PaginatedRequestParams())  # type: ignore[arg-type]
    assert isinstance(result, types.ListToolsResult)
    assert [t.name for t in result.tools] == ["echo"]


async def test_list_resources_and_prompts_register_handlers():
    server = _shimmed_server()

    @server.list_resources()
    async def handle_list_resources() -> list[types.Resource]:
        return []

    @server.list_prompts()
    async def handle_list_prompts() -> list[types.Prompt]:
        return []

    handlers = server._request_handlers  # type: ignore[attr-defined]
    assert handlers.get("resources/list") is not None
    assert handlers.get("prompts/list") is not None


async def test_call_tool_decorator_registers_handler():
    server = _shimmed_server()

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict | None
    ) -> list[types.TextContent]:
        return [types.TextContent(type="text", text=f"called {name} with {arguments}")]

    handlers = server._request_handlers  # type: ignore[attr-defined]
    entry = handlers.get("tools/call")
    assert entry is not None
    params = types.CallToolRequestParams(name="echo", arguments={"x": 1})
    result = await entry.handler(None, params)  # type: ignore[arg-type]
    assert isinstance(result, types.CallToolResult)
    assert result.content[0].text == "called echo with {'x': 1}"


def test_shimmed_decorators_support_browser_use_server_construction():
    """End-to-end: BrowserUseServer.__init__ must succeed under mcp 2.x."""
    from openhands.tools.browser_use.server import CustomBrowserUseServer

    server = CustomBrowserUseServer(session_timeout_minutes=1)
    assert server is not None
