"""Run the deterministic stdio MCP test server."""

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field


mcp = FastMCP("stdio-test-server")


@mcp.tool()
def fetch(
    url: Annotated[str, Field(description="URL to fetch.")],
    max_length: Annotated[
        int,
        Field(description="Maximum number of characters to return."),
    ] = 5000,
) -> str:
    """Fetch a URL."""
    result = f"Fetched {url}"
    return result[:max_length]


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
