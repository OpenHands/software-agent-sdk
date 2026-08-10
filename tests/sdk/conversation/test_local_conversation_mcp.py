"""Tests for how LocalConversation wires MCP servers into a running agent."""

from __future__ import annotations

import asyncio
import multiprocessing
import socket
import time
from collections.abc import Callable, Iterator, Sequence
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any, cast

import httpx
import mcp.types as mcp_types
import pytest
from fastmcp import FastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from pydantic import PrivateAttr, SecretStr
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.llm import Message, TextContent, TokenCallbackType
from openhands.sdk.llm.llm import LLMCallContext
from openhands.sdk.llm.llm_response import LLMResponse
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import MCPServer, coerce_mcp_config
from openhands.sdk.mcp.tool import MCPToolDefinition
from openhands.sdk.mcp.utils import MCPToolProvider
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool.tool import ToolDefinition


class ToolRecordingLLM(TestLLM):
    _tool_snapshots: list[list[str]] = PrivateAttr(default_factory=list)

    def completion(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        add_security_risk_prediction: bool = False,
        on_token: TokenCallbackType | None = None,
        call_context: LLMCallContext | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self._tool_snapshots.append(sorted(tool.name for tool in tools or []))
        return super().completion(
            messages,
            tools,
            add_security_risk_prediction,
            on_token,
            call_context,
            **kwargs,
        )


class InitializeCounter(Middleware):
    def __init__(self) -> None:
        self.count = 0

    async def on_initialize(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        self.count += 1
        return await call_next(context)


def _run_mcp_deployment(port: int, deployment: str, stateless_http: bool) -> None:
    server = FastMCP("refresh-test")
    initialize_counter = InitializeCounter()
    server.add_middleware(initialize_counter)

    @server.custom_route("/initialize-count", methods=["GET"])
    async def initialize_count(_request: Request) -> Response:
        return PlainTextResponse(str(initialize_counter.count))

    if deployment == "old":

        @server.tool()
        def old_tool() -> str:
            """Tool removed by the next deployment."""
            return "old"

        @server.tool(name="changing")
        def old_changing(old: str) -> str:
            """Old schema."""
            return old

    else:

        @server.tool()
        def new_tool() -> str:
            """Tool added by the next deployment."""
            return "new"

        @server.tool(name="changing")
        def new_changing(new: int) -> int:
            """New schema."""
            return new

    asyncio.run(
        server.run_http_async(
            host="127.0.0.1",
            port=port,
            transport="http",
            show_banner=False,
            path="/mcp",
            stateless_http=stateless_http,
        )
    )


@pytest.fixture
def deploy_mcp_server() -> Iterator[Callable[[str | None, bool], str]]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    process: BaseProcess | None = None

    def stop() -> None:
        nonlocal process
        if process is None:
            return
        process.terminate()
        process.join(timeout=10)
        assert not process.is_alive()
        process = None

    def deploy(version: str | None, stateless_http: bool) -> str:
        nonlocal process
        stop()
        url = f"http://127.0.0.1:{port}/mcp"
        if version is None:
            return url
        new_process = multiprocessing.get_context("spawn").Process(
            target=_run_mcp_deployment,
            args=(port, version, stateless_http),
        )
        new_process.start()
        process = new_process
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    return url
            if new_process.exitcode is not None:
                raise RuntimeError(
                    f"MCP {version} deployment exited with {new_process.exitcode}"
                )
            time.sleep(0.05)
        raise TimeoutError(f"MCP {version} deployment did not start")

    yield deploy
    stop()


class EmptyMCPClient:
    def __init__(self) -> None:
        self.tools: list[MCPToolDefinition] = []
        self._tools_reconciled_callback: Any = None
        self.closed = False

    def sync_close(self) -> None:
        self.closed = True


class RecordingMCPToolProvider:
    """Records every attempt to open an MCP connection."""

    def __init__(self, client: EmptyMCPClient | None = None) -> None:
        self.calls: list[dict[str, MCPServer]] = []
        self.client = client if client is not None else EmptyMCPClient()

    def create_tools(
        self,
        mcp_config: dict[str, MCPServer],
        timeout: float = 30.0,
        *,
        on_tools_changed: Any = None,
        on_tools_reconciled: Any = None,
    ) -> MCPClient:
        self.calls.append(mcp_config)
        self.client._tools_reconciled_callback = on_tools_reconciled
        return cast(MCPClient, self.client)


def test_disabling_every_server_skips_the_mcp_connection(tmp_path: Path) -> None:
    """The agent still starts; it just has no MCP servers to reach."""
    provider = RecordingMCPToolProvider()
    agent = Agent(
        llm=LLM(model="test-model", api_key=SecretStr("test-key")),
        tools=[],
        mcp_config=coerce_mcp_config({"fetch": {"command": "uvx", "enabled": False}}),
    )
    conversation = LocalConversation(
        agent=agent,
        workspace=str(tmp_path),
        visualizer=None,
        mcp_tool_provider=provider,
    )

    conversation._ensure_agent_ready()

    assert provider.calls == []
    conversation.close()


def test_reconciliation_targets_replaced_agent(tmp_path: Path) -> None:
    client = EmptyMCPClient()
    initial = MCPToolDefinition.create(
        mcp_tool=mcp_types.Tool(
            name="initial",
            description="initial",
            inputSchema={"type": "object", "properties": {}},
        ),
        mcp_client=cast(MCPClient, client),
    )[0]
    client.tools = [initial]
    conversation = LocalConversation(
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("test-key")),
            tools=[],
            include_default_tools=[],
            mcp_config=coerce_mcp_config({"fake": {"command": "true"}}),
        ),
        workspace=str(tmp_path),
        visualizer=None,
        mcp_tool_provider=RecordingMCPToolProvider(client),
    )
    conversation._ensure_agent_ready()
    old_agent = conversation.agent
    conversation.agent = old_agent.model_copy()
    replacement = MCPToolDefinition.create(
        mcp_tool=mcp_types.Tool(
            name="replacement",
            description="replacement",
            inputSchema={"type": "object", "properties": {}},
        ),
        mcp_client=cast(MCPClient, client),
    )[0]

    client._tools_reconciled_callback(cast(MCPClient, client), [replacement])

    assert set(conversation.agent.tools_map) == {"replacement"}
    assert set(old_agent.tools_map) == {"initial"}
    conversation.close()


class LegacyMCPToolProvider:
    """A custom provider written against the pre-reconciliation protocol."""

    def create_tools(
        self,
        mcp_config: dict[str, MCPServer],
        timeout: float = 30.0,
        *,
        on_tools_changed: Any = None,
    ) -> MCPClient:
        return cast(MCPClient, EmptyMCPClient())


def test_legacy_provider_without_on_tools_reconciled_still_works(
    tmp_path: Path,
) -> None:
    """A custom MCPToolProvider that predates on_tools_reconciled must not
    break; it just won't receive full-snapshot reconciliation."""
    conversation = LocalConversation(
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("test-key")),
            tools=[],
            include_default_tools=[],
            mcp_config=coerce_mcp_config({"fake": {"command": "true"}}),
        ),
        workspace=str(tmp_path),
        visualizer=None,
        # Deliberately incompatible with the current MCPToolProvider
        # protocol shape; that's the scenario under test.
        mcp_tool_provider=cast(MCPToolProvider, LegacyMCPToolProvider()),
    )

    conversation._ensure_agent_ready()

    conversation.close()


class _KwargsMCPToolProvider:
    """A provider that accepts arbitrary keywords via **kwargs."""

    def create_tools(
        self, mcp_config: dict[str, MCPServer], timeout: float = 30.0, **kwargs: Any
    ) -> MCPClient:
        return cast(MCPClient, EmptyMCPClient())


def test_provider_supports_on_tools_reconciled() -> None:
    from openhands.sdk.mcp.utils import (
        DefaultMCPToolProvider,
        provider_supports_on_tools_reconciled,
    )

    assert provider_supports_on_tools_reconciled(DefaultMCPToolProvider())
    assert provider_supports_on_tools_reconciled(RecordingMCPToolProvider())
    assert provider_supports_on_tools_reconciled(_KwargsMCPToolProvider())
    assert not provider_supports_on_tools_reconciled(
        cast(MCPToolProvider, LegacyMCPToolProvider())
    )


def test_refresh_discovers_tools_from_an_initially_empty_client(
    tmp_path: Path, monkeypatch
) -> None:
    client = EmptyMCPClient()
    conversation = LocalConversation(
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("test-key")),
            tools=[],
            include_default_tools=[],
            mcp_config=coerce_mcp_config({"fake": {"command": "true"}}),
        ),
        workspace=str(tmp_path),
        visualizer=None,
        mcp_tool_provider=RecordingMCPToolProvider(client),
    )
    conversation._ensure_agent_ready()
    discovered = MCPToolDefinition.create(
        mcp_tool=mcp_types.Tool(
            name="discovered",
            description="discovered after deployment",
            inputSchema={"type": "object", "properties": {}},
        ),
        mcp_client=cast(MCPClient, client),
    )[0]
    client.tools = [discovered]

    def refresh(client, timeout, *, on_tools_reconciled):
        on_tools_reconciled(client, client.tools)

    monkeypatch.setattr(
        "openhands.sdk.conversation.impl.local_conversation._refresh_mcp_client_tools",
        refresh,
    )

    conversation.refresh_mcp_tools()

    assert set(conversation.agent.tools_map) == {"discovered"}
    conversation.close()


def test_initialization_failure_closes_an_empty_mcp_client(
    tmp_path: Path, monkeypatch
) -> None:
    client = EmptyMCPClient()
    conversation = LocalConversation(
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("test-key")),
            tools=[],
            include_default_tools=[],
            mcp_config=coerce_mcp_config({"fake": {"command": "true"}}),
        ),
        workspace=tmp_path,
        visualizer=None,
        mcp_tool_provider=RecordingMCPToolProvider(client),
    )

    def fail_to_add_tools(_self, _tools):
        raise RuntimeError("failed to add runtime tools")

    monkeypatch.setattr(Agent, "add_runtime_tools", fail_to_add_tools)

    with pytest.raises(RuntimeError, match="failed to add runtime tools"):
        conversation._ensure_agent_ready()

    assert client.closed
    assert conversation._mcp_clients == []


@pytest.mark.parametrize("stateless_http", [False, True], ids=["stateful", "stateless"])
def test_refreshes_tools_after_mcp_deployment(
    tmp_path: Path,
    deploy_mcp_server: Callable[[str | None, bool], str],
    stateless_http: bool,
) -> None:
    url = deploy_mcp_server("old", stateless_http)
    llm = cast(
        ToolRecordingLLM,
        ToolRecordingLLM.from_messages(
            [Message(role="assistant", content=[TextContent(text="Done")])]
        ),
    )
    conversation = LocalConversation(
        agent=Agent(
            llm=llm,
            tools=[],
            include_default_tools=[],
            mcp_config=coerce_mcp_config(
                {"analysis": {"transport": "http", "url": url}}
            ),
        ),
        workspace=tmp_path,
        visualizer=None,
    )

    try:
        conversation.send_message("Inspect the deployed tools")
        assert set(conversation.agent.tools_map) == {"changing", "old_tool"}
        conversation_id = conversation.id
        events = list(conversation.state.events)
        workspace = conversation.workspace

        new_url = deploy_mcp_server("new", stateless_http)
        conversation.refresh_mcp_tools()

        assert conversation.id == conversation_id
        assert list(conversation.state.events) == events
        assert conversation.workspace is workspace
        assert set(conversation.agent.tools_map) == {"changing", "new_tool"}
        changing = conversation.agent.tools_map["changing"]
        assert changing.description == "New schema."
        changing.action_from_arguments({"new": 7})
        initialize_count_url = new_url.removesuffix("/mcp") + "/initialize-count"
        expected_initializations = 0 if stateless_http else 1
        assert int(httpx.get(initialize_count_url).text) == expected_initializations

        conversation.run()

        assert llm._tool_snapshots == [["changing", "new_tool"]]
    finally:
        conversation.close()


def test_refresh_recovers_after_server_was_temporarily_unavailable(
    tmp_path: Path,
    deploy_mcp_server: Callable[[str | None, bool], str],
) -> None:
    url = deploy_mcp_server("old", True)
    conversation = LocalConversation(
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("test-key")),
            tools=[],
            include_default_tools=[],
            mcp_config=coerce_mcp_config(
                {"analysis": {"transport": "http", "url": url}}
            ),
        ),
        workspace=tmp_path,
        visualizer=None,
    )

    try:
        conversation._ensure_agent_ready()
        deploy_mcp_server(None, True)
        with pytest.raises(httpx.ConnectError):
            conversation.refresh_mcp_tools()

        deploy_mcp_server("new", True)
        conversation.refresh_mcp_tools()

        assert set(conversation.agent.tools_map) == {"changing", "new_tool"}
    finally:
        conversation.close()
