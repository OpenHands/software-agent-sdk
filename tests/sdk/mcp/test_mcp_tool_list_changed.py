"""Tests for ``notifications/tools/list_changed`` handling.

Some MCP servers (e.g. Datadog's hosted MCP server) use progressive
disclosure: they expose a small gateway toolset at connect time and register
additional tools only after a skill-loading tool is invoked, signalling the
change with ``notifications/tools/list_changed``. These tests verify that
``create_mcp_tools`` subscribes to that notification, re-lists tools, and
invokes the ``on_tools_changed`` callback with the newly added tools.

The end-to-end notification delivery over streamable HTTP is exercised
against a real FastMCP server below. Because delivering a server-initiated
notification reliably mid-session depends on the server keeping the SSE
notification stream open (the way Datadog's hosted server does), the core
diff/refresh logic is additionally covered by focused unit tests that do not
depend on transport timing.
"""

import asyncio
import socket
import threading
import time
from typing import cast

import mcp.types as mcp_types
import pytest
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_context
from pydantic import ValidationError

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.llm import TextContent
from openhands.sdk.mcp import MCPClient, create_mcp_tools
from openhands.sdk.mcp.config import coerce_mcp_config
from openhands.sdk.mcp.tool import MCPToolDefinition
from openhands.sdk.mcp.utils import (
    _refresh_tools,
    _ToolListChangedHandler,
)


def _native_config(raw: dict) -> dict:
    """Coerce a FastMCP-shaped config dict to native ``dict[str, MCPServer]``."""
    return coerce_mcp_config(raw)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 10.0) -> None:
    import httpx

    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/mcp"
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=1.0) as client:
                client.get(url)
            return
        except Exception as e:  # noqa: BLE001
            last_error = e
            time.sleep(0.1)
    raise RuntimeError(f"MCP test server on port {port} did not start: {last_error}")


def _make_mcp_tool(name: str) -> mcp_types.Tool:
    return mcp_types.Tool(
        name=name,
        description=f"tool {name}",
        inputSchema={"type": "object", "properties": {}},
    )


class _FakeClient:
    """Minimal stand-in for ``MCPClient`` used by ``_refresh_tools``.

    ``_refresh_tools`` only needs ``list_tools()`` (async) and the
    ``_tools`` / ``_closed`` attributes, so a lightweight fake keeps the diff
    logic unit-testable without spinning up a real server.
    """

    def __init__(self, tools: list[mcp_types.Tool]):
        self._server_tools = list(tools)
        self._tools: list[MCPToolDefinition] = []
        self._closed = False
        self._tools_reconciled_callback = None

    async def list_tools(self) -> list[mcp_types.Tool]:
        return list(self._server_tools)


class _ConcreteAgent(AgentBase):
    """Minimal concrete ``AgentBase`` for unit-testing runtime helpers.

    ``AgentBase`` is abstract (``step``) and a frozen pydantic model, so
    tests that only exercise runtime tool updates use this stub to bypass full
    agent construction.
    """

    def __init__(self, _initialized: bool, _tools):  # noqa: ANN001
        # Skip pydantic validation; set the attributes the helpers read.
        object.__setattr__(self, "_initialized", _initialized)
        object.__setattr__(self, "_tools", _tools)
        object.__setattr__(self, "_tools_lock", threading.RLock())
        object.__setattr__(self, "filter_tools_regex", None)

    def step(self, conversation, on_event, on_token=None):  # noqa: ARG002, ANN001
        raise NotImplementedError


def test_refresh_tools_reports_only_new_tools():
    """``_refresh_tools`` diffs against existing tools and reports additions."""
    client = _FakeClient([_make_mcp_tool("a"), _make_mcp_tool("b")])
    # Simulate a prior connect that already discovered ``a``.
    client._tools = list(
        MCPToolDefinition.create(mcp_tool=_make_mcp_tool("a"), mcp_client=client)  # type: ignore[arg-type]
    )

    received: list[list[str]] = []

    async def run():
        await _refresh_tools(
            client,  # type: ignore[arg-type]
            on_tools_changed=lambda tools: received.append([t.name for t in tools]),
        )

    asyncio.new_event_loop().run_until_complete(run())

    assert {t.name for t in client._tools} == {"a", "b"}
    assert received == [["b"]]


def test_refresh_tools_drops_removed_tools():
    """Tools the server no longer advertises are dropped from the client."""
    client = _FakeClient([_make_mcp_tool("a")])
    client._tools = list(
        MCPToolDefinition.create(mcp_tool=_make_mcp_tool("a"), mcp_client=client)  # type: ignore[arg-type]
    ) + list(
        MCPToolDefinition.create(mcp_tool=_make_mcp_tool("gone"), mcp_client=client)  # type: ignore[arg-type]
    )

    async def run():
        await _refresh_tools(client, on_tools_changed=None)  # type: ignore[arg-type]

    asyncio.new_event_loop().run_until_complete(run())

    assert {t.name for t in client._tools} == {"a"}


def test_refresh_tools_reconciles_updates_and_removals():
    """The full snapshot callback receives updated definitions and removals."""
    old_tool = mcp_types.Tool(
        name="changing",
        description="old schema",
        inputSchema={
            "type": "object",
            "properties": {"old": {"type": "string"}},
            "required": ["old"],
        },
    )
    new_tool = mcp_types.Tool(
        name="changing",
        description="new schema",
        inputSchema={
            "type": "object",
            "properties": {"new": {"type": "integer"}},
            "required": ["new"],
        },
    )
    client = _FakeClient([new_tool])
    old_definition = MCPToolDefinition.create(
        mcp_tool=old_tool,
        mcp_client=cast(MCPClient, client),
    )[0]
    old_definition.action_from_arguments({"old": "value"})
    client._tools = [
        old_definition,
        MCPToolDefinition.create(
            mcp_tool=_make_mcp_tool("gone"),
            mcp_client=cast(MCPClient, client),
        )[0],
    ]
    received: list[tuple[object, list[MCPToolDefinition]]] = []

    async def run():
        await _refresh_tools(
            cast(MCPClient, client),
            on_tools_reconciled=lambda owner, tools: received.append(
                (owner, list(tools))
            ),
        )

    asyncio.new_event_loop().run_until_complete(run())

    assert len(received) == 1
    owner, tools = received[0]
    assert owner is client
    assert [tool.name for tool in tools] == ["changing"]
    assert tools[0].description == "new schema"
    tools[0].action_from_arguments({"new": 42})
    with pytest.raises(ValidationError):
        tools[0].action_from_arguments({"old": "value"})


def test_refresh_tools_no_callback_still_reconciles():
    """Without a callback the client tool list is still kept in sync."""
    client = _FakeClient([_make_mcp_tool("a"), _make_mcp_tool("b")])

    async def run():
        await _refresh_tools(client, on_tools_changed=None)  # type: ignore[arg-type]

    asyncio.new_event_loop().run_until_complete(run())

    assert {t.name for t in client._tools} == {"a", "b"}


def test_handler_invokes_refresh_on_list_changed():
    """``_ToolListChangedHandler`` re-lists and calls back on notification."""
    client = _FakeClient([_make_mcp_tool("a"), _make_mcp_tool("b")])
    client._tools = list(
        MCPToolDefinition.create(mcp_tool=_make_mcp_tool("a"), mcp_client=client)  # type: ignore[arg-type]
    )
    received: list[list[str]] = []
    handler = _ToolListChangedHandler(
        client=client,  # type: ignore[arg-type]
        on_tools_changed=lambda tools: received.append([t.name for t in tools]),
    )

    async def run():
        await handler.on_tool_list_changed(mcp_types.ToolListChangedNotification())
        deadline = time.time() + 1.0
        while time.time() < deadline and not received:
            await asyncio.sleep(0.01)

    asyncio.new_event_loop().run_until_complete(run())

    assert {t.name for t in client._tools} == {"a", "b"}
    assert received == [["b"]]


def test_handler_skips_when_client_closed():
    """A notification arriving after close is ignored."""
    client = _FakeClient([_make_mcp_tool("a")])
    client._closed = True
    handler = _ToolListChangedHandler(
        client=client,  # type: ignore[arg-type]
        on_tools_changed=lambda tools: None,
    )

    asyncio.new_event_loop().run_until_complete(
        handler.on_tool_list_changed(mcp_types.ToolListChangedNotification())
    )

    assert client._tools == []


@pytest.fixture
def progressive_server():
    """An MCP server that adds a tool and sends ``tools/list_changed``.

    Calling ``register_extra_tool`` adds a second tool and sends a
    ``notifications/tools/list_changed`` notification to the current session,
    mimicking Datadog's progressive-disclosure behavior.
    """
    mcp = FastMCP("progressive-test-server")

    async def notify_list_changed() -> None:
        notification = mcp_types.ToolListChangedNotification()
        loop = asyncio.get_running_loop()
        loop.create_task(get_context().send_notification(notification))

    @mcp.tool()
    async def gateway() -> str:
        """Always-available gateway tool."""
        return "gateway-ok"

    @mcp.tool()
    async def register_extra_tool() -> str:
        """Register a second tool and notify the client the list changed."""

        @mcp.tool()
        def extra(value: int) -> int:
            """Tool added after the client connected."""
            return value * 2

        # Send the notification as a fire-and-forget task so the tool-call
        # response is flushed first; the notification rides the long-lived SSE
        # stream the client keeps open for server notifications.
        await notify_list_changed()
        return "registered"

    @mcp.tool()
    async def remove_extra_tool() -> str:
        """Remove the dynamically registered tool and notify the client."""
        mcp.local_provider.remove_tool("extra")
        await notify_list_changed()
        return "removed"

    port = _find_free_port()

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            mcp.run_http_async(
                host="127.0.0.1",
                port=port,
                transport="http",
                show_banner=False,
                path="/mcp",
            )
        )

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    _wait_for_port(port)
    yield port


def test_no_callback_still_connects(progressive_server: int):
    """``on_tools_changed=None`` must not break tool creation."""
    port = progressive_server
    config = _native_config(
        {
            "mcpServers": {
                "progressive": {
                    "transport": "http",
                    "url": f"http://127.0.0.1:{port}/mcp",
                }
            }
        }
    )
    with create_mcp_tools(config, timeout=10.0) as client:
        names = {t.name for t in client}
        assert "gateway" in names
        assert "register_extra_tool" in names


def test_list_changed_notification_reconciles_readded_agent_tool(
    progressive_server: int,
):
    """A re-added tool replaces the agent's stale MCP definition."""
    port = progressive_server
    config = _native_config(
        {
            "mcpServers": {
                "progressive": {
                    "transport": "http",
                    "url": f"http://127.0.0.1:{port}/mcp",
                }
            }
        }
    )
    received: list[str] = []
    agent = _ConcreteAgent(_initialized=True, _tools={})

    def on_tools_changed(tools):  # noqa: ANN001
        received.extend(tool.name for tool in tools)

    def on_tools_reconciled(client, tools):  # noqa: ANN001
        agent._on_mcp_tools_reconciled(client, tools)

    with create_mcp_tools(
        config,
        timeout=10.0,
        on_tools_changed=on_tools_changed,
        on_tools_reconciled=on_tools_reconciled,
    ) as client:
        agent.add_runtime_tools(client.tools)
        initial_names = {t.name for t in client.tools}
        assert "gateway" in initial_names
        assert "register_extra_tool" in initial_names
        assert "remove_extra_tool" in initial_names
        assert "extra" not in initial_names

        register_tool = next(t for t in client.tools if t.name == "register_extra_tool")
        register_observation = register_tool(register_tool.action_from_arguments({}))
        assert not register_observation.is_error

        deadline = time.time() + 10.0
        current_names = {t.name for t in client.tools}
        while time.time() < deadline and "extra" not in current_names:
            time.sleep(0.1)
            current_names = {t.name for t in client.tools}

        assert "extra" in current_names
        assert received == ["extra"]

        extra_tool = next(t for t in client.tools if t.name == "extra")
        extra_observation = extra_tool(extra_tool.action_from_arguments({"value": 21}))
        extra_text = "\n".join(
            block.text
            for block in extra_observation.content
            if isinstance(block, TextContent)
        )
        assert not extra_observation.is_error
        assert "42" in extra_text

        first_agent_extra = agent.tools_map["extra"]
        remove_tool = next(t for t in client.tools if t.name == "remove_extra_tool")
        remove_observation = remove_tool(remove_tool.action_from_arguments({}))
        assert not remove_observation.is_error

        deadline = time.time() + 10.0
        while time.time() < deadline and any(
            tool.name == "extra" for tool in client.tools
        ):
            time.sleep(0.1)

        assert all(tool.name != "extra" for tool in client.tools)
        assert "extra" not in agent.tools_map

        register_observation = register_tool(register_tool.action_from_arguments({}))
        assert not register_observation.is_error

        deadline = time.time() + 10.0
        while time.time() < deadline and "extra" not in agent.tools_map:
            time.sleep(0.1)

        readded_agent_extra = agent.tools_map["extra"]
        readded_client_extra = next(t for t in client.tools if t.name == "extra")
        assert readded_agent_extra is readded_client_extra
        assert readded_agent_extra is not first_agent_extra
        assert received == ["extra", "extra"]

        readded_observation = readded_agent_extra(
            readded_agent_extra.action_from_arguments({"value": 21})
        )
        assert not readded_observation.is_error


def test_on_mcp_tools_changed_registers_runtime_tools():
    """``AgentBase._on_mcp_tools_changed`` forwards to ``add_runtime_tools``."""
    agent = _ConcreteAgent(_initialized=True, _tools={})
    client = _FakeClient([])
    tool = MCPToolDefinition.create(
        mcp_tool=_make_mcp_tool("dynamic"),
        mcp_client=cast(MCPClient, client),
    )[0]

    agent._on_mcp_tools_changed([tool])

    assert agent.tools_map["dynamic"] is tool


def test_on_mcp_tools_changed_skips_when_not_initialized():
    """Before initialization, notifications are dropped, not crashed on."""
    agent = _ConcreteAgent(_initialized=False, _tools=None)

    # Must not raise even though add_runtime_tools would warn.
    agent._on_mcp_tools_changed([])  # type: ignore[arg-type]


def test_on_mcp_tools_reconciled_does_not_remove_other_client_tools():
    """A client snapshot only replaces tools owned by that client."""
    first_client = _FakeClient([])
    second_client = _FakeClient([])
    first_tool = MCPToolDefinition.create(
        mcp_tool=_make_mcp_tool("first"),
        mcp_client=cast(MCPClient, first_client),
    )[0]
    second_tool = MCPToolDefinition.create(
        mcp_tool=_make_mcp_tool("second"),
        mcp_client=cast(MCPClient, second_client),
    )[0]
    replacement = MCPToolDefinition.create(
        mcp_tool=_make_mcp_tool("replacement"),
        mcp_client=cast(MCPClient, first_client),
    )[0]
    agent = _ConcreteAgent(
        _initialized=True,
        _tools={"first": first_tool, "second": second_tool},
    )

    agent._on_mcp_tools_reconciled(
        cast(MCPClient, first_client),
        [replacement],
    )

    assert set(agent.tools_map) == {"replacement", "second"}
    assert agent.tools_map["second"] is second_tool


def test_on_mcp_tools_reconciled_filters_before_conflict_check():
    first_client = _FakeClient([])
    second_client = _FakeClient([])
    blocked = MCPToolDefinition.create(
        mcp_tool=_make_mcp_tool("blocked"),
        mcp_client=cast(MCPClient, first_client),
    )[0]
    filtered_conflict = MCPToolDefinition.create(
        mcp_tool=_make_mcp_tool("blocked"),
        mcp_client=cast(MCPClient, second_client),
    )[0]
    allowed = MCPToolDefinition.create(
        mcp_tool=_make_mcp_tool("allowed"),
        mcp_client=cast(MCPClient, second_client),
    )[0]
    agent = _ConcreteAgent(_initialized=True, _tools={"blocked": blocked})
    object.__setattr__(agent, "filter_tools_regex", r"^allowed$")

    agent._on_mcp_tools_reconciled(
        cast(MCPClient, second_client),
        [filtered_conflict, allowed],
    )

    assert set(agent.tools_map) == {"blocked", "allowed"}


def test_on_mcp_tools_reconciled_serializes_client_updates():
    first_snapshot = threading.Event()
    second_snapshot = threading.Event()

    class CoordinatedDict(dict[str, MCPToolDefinition]):
        def __init__(self, values: dict[str, MCPToolDefinition]):
            super().__init__(values)
            self.local = threading.local()

        def items(self):  # type: ignore[override]  # noqa: ANN201
            snapshot = list(super().items())
            count = getattr(self.local, "count", 0) + 1
            self.local.count = count
            if count == 2:
                if first_snapshot.is_set():
                    second_snapshot.set()
                else:
                    first_snapshot.set()
                    second_snapshot.wait(0.2)
            return snapshot

    first_client = _FakeClient([])
    second_client = _FakeClient([])
    first_old = MCPToolDefinition.create(
        mcp_tool=_make_mcp_tool("first_old"),
        mcp_client=cast(MCPClient, first_client),
    )[0]
    second_old = MCPToolDefinition.create(
        mcp_tool=_make_mcp_tool("second_old"),
        mcp_client=cast(MCPClient, second_client),
    )[0]
    first_new = MCPToolDefinition.create(
        mcp_tool=_make_mcp_tool("first_new"),
        mcp_client=cast(MCPClient, first_client),
    )[0]
    second_new = MCPToolDefinition.create(
        mcp_tool=_make_mcp_tool("second_new"),
        mcp_client=cast(MCPClient, second_client),
    )[0]
    agent = _ConcreteAgent(
        _initialized=True,
        _tools=CoordinatedDict({"first_old": first_old, "second_old": second_old}),
    )
    threads = [
        threading.Thread(
            target=agent._on_mcp_tools_reconciled,
            args=(cast(MCPClient, first_client), [first_new]),
        ),
        threading.Thread(
            target=agent._on_mcp_tools_reconciled,
            args=(cast(MCPClient, second_client), [second_new]),
        ),
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert set(agent.tools_map) == {"first_new", "second_new"}
