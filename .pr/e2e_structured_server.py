"""Stdio MCP server for the structured-result live e2e.

Built on the protocol library's low-level server, which follows the
2026-07-28 revision (structured results may be any JSON value).
list_open_ports returns a top-level JSON array as its structured result;
feature_flags returns an object holding JSON booleans.
"""

import asyncio

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


TOOLS = [
    types.Tool(
        name="list_open_ports",
        description="List the TCP ports open on the build host.",
        input_schema={"type": "object", "properties": {}},
    ),
    types.Tool(
        name="feature_flags",
        description="Report the build's feature flags.",
        input_schema={"type": "object", "properties": {}},
    ),
]


async def on_list_tools(_ctx, _params):
    return types.ListToolsResult(tools=TOOLS)


async def on_call_tool(_ctx, params):
    if params.name == "list_open_ports":
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="3 ports open")],
            structured_content=[22, 80, 443],
        )
    if params.name == "feature_flags":
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="2 flags read")],
            structured_content={"beta_enabled": True, "dark_mode": False},
        )
    raise ValueError(f"unknown tool {params.name}")


server = Server(
    "e2e-structured", on_list_tools=on_list_tools, on_call_tool=on_call_tool
)


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
