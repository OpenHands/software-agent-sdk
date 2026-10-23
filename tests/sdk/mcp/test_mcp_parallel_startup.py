"""Multi-server MCP startup: concurrent, and isolated per server."""

import asyncio
import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from fastmcp import Client, FastMCP
from starlette.middleware.base import BaseHTTPMiddleware

from openhands.sdk.mcp import create_mcp_tools
from openhands.sdk.mcp.config import coerce_mcp_config
from openhands.sdk.mcp.utils import _prepare_mcp_config


HANG_SECS = 3600.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            httpx.get(f"http://127.0.0.1:{port}/", timeout=0.2)
            return
        except httpx.ConnectError:
            time.sleep(0.05)
        except httpx.HTTPError:
            return
    raise RuntimeError(f"Server failed to start on port {port}")


class _DelayInitialize(BaseHTTPMiddleware):
    """Holds the MCP ``initialize`` request for ``delay`` seconds."""

    def __init__(self, app, delay: float):
        super().__init__(app)
        self.delay = delay

    async def dispatch(self, request, call_next):
        body = (await request.body()).replace(b" ", b"")
        if b'"method":"initialize"' in body:
            await asyncio.sleep(self.delay)
        return await call_next(request)


@pytest.fixture
def start_server() -> Iterator:
    """Start in-process HTTP MCP servers whose handshake takes ``delay`` s."""
    servers: list[tuple[uvicorn.Server, threading.Thread]] = []

    def start(delay: float = 0.0) -> str:
        mcp = FastMCP("test-server")

        @mcp.tool()
        def greet(name: str) -> str:
            """Greet someone by name."""
            return f"Hello, {name}!"

        @mcp.tool()
        def add_numbers(a: int, b: int) -> int:
            """Add two numbers together."""
            return a + b

        app = mcp.http_app(path="/mcp")
        if delay:
            app.add_middleware(_DelayInitialize, delay=delay)
        port = _free_port()
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="error",
                timeout_graceful_shutdown=1,
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        _wait_for_port(port)
        servers.append((server, thread))
        return f"http://127.0.0.1:{port}/mcp"

    yield start

    for server, _ in servers:
        server.should_exit = True
    for _, thread in servers:
        thread.join(timeout=5)


def _config(urls: dict[str, str]):
    return coerce_mcp_config(
        {name: {"transport": "http", "url": url} for name, url in urls.items()}
    )


def test_servers_connect_concurrently(start_server):
    """Wall time follows the slowest server, not the sum of all servers."""
    delay = 1.5
    config = _config({name: start_server(delay=delay) for name in ("a", "b", "c")})

    started = time.monotonic()
    with create_mcp_tools(config, timeout=20.0) as client:
        elapsed = time.monotonic() - started
        names = {tool.name for tool in client.tools}

    assert names == {f"{s}_{t}" for s in "abc" for t in ("greet", "add_numbers")}
    assert elapsed < 2 * delay, f"servers connected one after another ({elapsed}s)"


def test_hanging_server_is_dropped_and_others_kept(start_server, caplog):
    config = _config(
        {
            "fast_one": start_server(),
            "silent": start_server(delay=HANG_SECS),
            "fast_two": start_server(),
        }
    )

    # Before, the silent server consumed the whole budget and the call raised
    # MCPTimeoutError, losing every server's tools.
    with caplog.at_level("WARNING"):
        with create_mcp_tools(config, timeout=5.0) as client:
            names = [tool.name for tool in client.tools]

            greet = next(t for t in client.tools if t.name == "fast_two_greet")
            assert greet.executor is not None
            observation = greet.executor(greet.action_from_arguments({"name": "x"}))

    assert sorted(names) == [
        "fast_one_add_numbers",
        "fast_one_greet",
        "fast_two_add_numbers",
        "fast_two_greet",
    ]
    assert not observation.is_error
    assert "Hello, x!" in observation.text
    assert "'silent' did not connect within 4.0 seconds" in caplog.text


def test_failing_server_is_dropped_and_others_kept(start_server, caplog):
    config = _config(
        {
            "broken": f"http://127.0.0.1:{_free_port()}/mcp",
            "working": start_server(),
        }
    )

    with caplog.at_level("WARNING"):
        with create_mcp_tools(config, timeout=10.0) as client:
            names = sorted(tool.name for tool in client.tools)

    assert names == ["working_add_numbers", "working_greet"]
    assert "'broken' failed to connect" in caplog.text


@pytest.mark.parametrize("server_names", [("only",), ("first", "second")])
def test_tool_names_match_fastmcp_config_client(start_server, server_names):
    """Names (and order) are those FastMCP's own config transport produces."""
    config = _config({name: start_server() for name in server_names})

    async def fastmcp_tool_names() -> list[str]:
        async with Client(_prepare_mcp_config(config)) as fastmcp_client:
            return [tool.name for tool in await fastmcp_client.list_tools()]

    with create_mcp_tools(config, timeout=10.0) as client:
        names = [tool.name for tool in client.tools]

    assert names == asyncio.run(fastmcp_tool_names())
    if len(server_names) == 1:
        assert sorted(names) == ["add_numbers", "greet"]
    else:
        assert sorted(names) == [
            "first_add_numbers",
            "first_greet",
            "second_add_numbers",
            "second_greet",
        ]
