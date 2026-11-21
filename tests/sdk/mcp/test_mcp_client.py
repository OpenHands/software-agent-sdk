import asyncio

import pytest

from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.exceptions import MCPError


class _EnterOnlyClient(MCPClient):
    def __init__(self) -> None:
        self.entered = False

    async def __aenter__(self) -> "_EnterOnlyClient":
        self.entered = True
        return self


class _FailingEnterClient(MCPClient):
    def __init__(self, error: RuntimeError) -> None:
        self.error = error

    async def __aenter__(self) -> "_FailingEnterClient":
        raise self.error


def test_connect_uses_fastmcp_public_context_manager() -> None:
    client = _EnterOnlyClient()

    asyncio.run(client.connect())

    assert client.entered
    assert "_session_state" not in client.__dict__


def test_connect_wraps_runtime_error_as_mcp_error() -> None:
    original = RuntimeError("transport failed")
    client = _FailingEnterClient(original)

    with pytest.raises(MCPError) as exc_info:
        asyncio.run(client.connect())

    assert exc_info.value.__cause__ is original
