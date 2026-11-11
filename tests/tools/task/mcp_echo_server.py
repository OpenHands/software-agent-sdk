"""Stdio MCP server for sub-agent MCP inheritance tests.

Usage: ``python mcp_echo_server.py TOOL_NAME [STARTED_MARKER]``. It serves one
tool, ``TOOL_NAME``, that echoes its text and the ``MCP_TOKEN`` it was started
with, and touches ``STARTED_MARKER`` on startup so a test can tell whether
anything connected to it.
"""

import os
import sys
from pathlib import Path

from fastmcp import FastMCP


tool_name = sys.argv[1]
if len(sys.argv) > 2:
    Path(sys.argv[2]).touch()

mcp = FastMCP(f"{tool_name}-server")


@mcp.tool(name=tool_name)
def echo(text: str) -> str:
    """Echo text with the token this server was started with."""
    return f"{tool_name}:{text}:token={os.environ.get('MCP_TOKEN', '')}"


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
