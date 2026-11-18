"""Tests for how LocalConversation wires MCP servers into a running agent."""

import asyncio
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast

import mcp.types as mcp_types
import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation import ConversationExecutionStatus
from openhands.sdk.conversation.impl import local_conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import MCPServer, coerce_mcp_config
from openhands.sdk.mcp.definition import MCPToolObservation
from openhands.sdk.mcp.exceptions import MCPError, MCPServerStartError
from openhands.sdk.mcp.tool import MCPToolDefinition
from openhands.sdk.mcp.utils import MCPToolProvider
from openhands.sdk.testing import TestLLM


REPO_ROOT = Path(__file__).resolve().parents[3]
FETCH_SERVER = {
    "command": sys.executable,
    "args": ["-m", "tests.sdk.mcp.stdio_test_server"],
    "cwd": str(REPO_ROOT),
}
UNRESPONSIVE_SERVER = {
    "command": sys.executable,
    "args": ["-c", "import sys; sys.stdin.read()"],
}
# Long enough for the fetch server to start on a busy CI runner.
STARTUP_TIMEOUT = 8
STARTUP_FAILED = "MCP server startup failed for"


class EmptyMCPClient:
    def __init__(self) -> None:
        self.tools: list[MCPToolDefinition] = []
        self._tools_reconciled_callback: Any = None

    def sync_close(self) -> None:
        pass


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


def _mcp_tool(client: EmptyMCPClient, name: str) -> MCPToolDefinition:
    return MCPToolDefinition.create(
        mcp_tool=mcp_types.Tool(
            name=name,
            description=name,
            input_schema={"type": "object", "properties": {}},
        ),
        mcp_client=cast(MCPClient, client),
    )[0]


def test_add_mcp_servers_registers_modern_runtime_tools(tmp_path: Path) -> None:
    client = EmptyMCPClient()
    client.tools = [_mcp_tool(client, "fetch")]
    provider = RecordingMCPToolProvider(client)
    conversation = LocalConversation(
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("test-key")),
            tools=[],
            include_default_tools=[],
        ),
        workspace=str(tmp_path),
        visualizer=None,
        mcp_tool_provider=provider,
    )
    conversation._ensure_agent_ready()

    conversation.add_mcp_servers({"docs": MCPServer(command="true")})

    assert [set(call) for call in provider.calls] == [{"docs"}]
    assert "docs" in conversation.agent.mcp_config
    assert _mcp_tool_names(conversation) == {"docs_fetch"}
    conversation.close()


def test_add_mcp_servers_rejects_sse_transport(tmp_path: Path) -> None:
    provider = RecordingMCPToolProvider()
    conversation = LocalConversation(
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("test-key")),
            tools=[],
            include_default_tools=[],
        ),
        workspace=str(tmp_path),
        visualizer=None,
        mcp_tool_provider=provider,
    )
    conversation._ensure_agent_ready()

    with pytest.raises(ValueError, match="SSE transport"):
        conversation.add_mcp_servers(
            {"events": MCPServer(url="http://example.test/sse", transport="sse")}
        )

    assert provider.calls == []
    assert conversation.agent.mcp_config == {}
    conversation.close()


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        # Exits at once, so the connection closes during the handshake.
        (sys.executable, "Connection closed"),
        ("/nonexistent/qa-missing-bin", "No such file or directory"),
    ],
    ids=["exits-at-once", "missing-executable"],
)
def test_add_mcp_servers_names_a_server_that_fails_to_start(
    tmp_path: Path, command: str, reason: str
) -> None:
    conversation = LocalConversation(
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("test-key")),
            tools=[],
            include_default_tools=[],
        ),
        workspace=str(tmp_path),
        visualizer=None,
    )
    conversation._ensure_agent_ready()
    server = MCPServer(command=command, args=["-c", "pass"])

    with pytest.raises(MCPServerStartError) as raised:
        conversation.add_mcp_servers({"dies": server})

    assert isinstance(raised.value, MCPError)
    assert raised.value.server_name == "dies"
    assert str(raised.value).startswith("Could not start MCP server dies: ")
    assert reason in str(raised.value)
    assert conversation.agent.mcp_config == {}
    conversation.close()


class ClosingMCPClient(EmptyMCPClient):
    def __init__(self) -> None:
        super().__init__()
        self.closed = 0

    def sync_close(self) -> None:
        self.closed += 1


class FailingSecondMCPToolProvider(RecordingMCPToolProvider):
    """Starts the first server, then fails with an error quoting a secret."""

    def __init__(self, error: str) -> None:
        self.started = ClosingMCPClient()
        self.started.tools = [_mcp_tool(self.started, "fetch")]
        super().__init__(self.started)
        self.error = error

    def create_tools(
        self,
        mcp_config: dict[str, MCPServer],
        timeout: float = 30.0,
        *,
        on_tools_changed: Any = None,
        on_tools_reconciled: Any = None,
    ) -> MCPClient:
        if self.calls:
            self.calls.append(mcp_config)
            raise RuntimeError(self.error)
        return super().create_tools(
            mcp_config,
            timeout,
            on_tools_changed=on_tools_changed,
            on_tools_reconciled=on_tools_reconciled,
        )


def test_add_mcp_servers_start_failure_closes_started_servers_and_masks_secrets(
    tmp_path: Path,
) -> None:
    provider = FailingSecondMCPToolProvider(
        "refused https://mcp.example/?token=qa-secret-value"
    )
    conversation = LocalConversation(
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("test-key")),
            tools=[],
            include_default_tools=[],
        ),
        workspace=str(tmp_path),
        visualizer=None,
        mcp_tool_provider=provider,
        secrets={"MCP_TOKEN": "qa-secret-value"},
    )
    conversation._ensure_agent_ready()

    with pytest.raises(MCPServerStartError) as raised:
        conversation.add_mcp_servers(
            {
                "first": MCPServer(command="true"),
                "second": MCPServer(url="https://mcp.example/?token=${MCP_TOKEN}"),
            }
        )

    assert raised.value.server_name == "second"
    assert "qa-secret-value" not in str(raised.value)
    assert [set(call) for call in provider.calls] == [{"first"}, {"second"}]
    assert provider.started.closed == 1
    assert conversation.agent.mcp_config == {}
    assert _mcp_tool_names(conversation) == set()
    conversation.close()


def test_add_mcp_servers_accepts_running_conversation(tmp_path: Path) -> None:
    client = EmptyMCPClient()
    client.tools = [_mcp_tool(client, "fetch")]
    provider = RecordingMCPToolProvider(client)
    conversation = LocalConversation(
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("test-key")),
            tools=[],
            include_default_tools=[],
        ),
        workspace=str(tmp_path),
        visualizer=None,
        mcp_tool_provider=provider,
    )
    conversation._ensure_agent_ready()
    conversation.state.transition(ConversationExecutionStatus.RUNNING, "test")

    conversation.add_mcp_servers({"docs": MCPServer(command="true")})

    assert [set(call) for call in provider.calls] == [{"docs"}]
    assert conversation.state.execution_status == ConversationExecutionStatus.RUNNING
    assert "docs" in conversation.agent.mcp_config
    assert _mcp_tool_names(conversation) == {"docs_fetch"}
    conversation.close()


def test_reconciliation_targets_replaced_agent(tmp_path: Path) -> None:
    client = EmptyMCPClient()
    initial = MCPToolDefinition.create(
        mcp_tool=mcp_types.Tool(
            name="initial",
            description="initial",
            input_schema={"type": "object", "properties": {}},
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
            input_schema={"type": "object", "properties": {}},
        ),
        mcp_client=cast(MCPClient, client),
    )[0]

    client._tools_reconciled_callback(cast(MCPClient, client), [replacement])

    updated_mcp_tools = {
        name
        for name, tool in conversation.agent.tools_map.items()
        if isinstance(tool, MCPToolDefinition)
    }
    old_mcp_tools = {
        name
        for name, tool in old_agent.tools_map.items()
        if isinstance(tool, MCPToolDefinition)
    }
    assert updated_mcp_tools == {"replacement"}
    assert old_mcp_tools == {"initial"}
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


class FailingMCPToolProvider:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def create_tools(
        self,
        mcp_config: dict[str, MCPServer],
        timeout: float = 30.0,
        *,
        on_tools_changed: Any = None,
        on_tools_reconciled: Any = None,
    ) -> MCPClient:
        raise self.error


@pytest.mark.parametrize(
    "error",
    [
        MCPError("MCP Connection Failure"),
        OSError("offline"),
        RuntimeError("unhealthy server"),
    ],
)
def test_mcp_startup_failure_does_not_abort_conversation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    conversation = LocalConversation(
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("test-key")),
            tools=[],
            include_default_tools=[],
            mcp_config=coerce_mcp_config({"broken": {"command": "missing-mcp"}}),
        ),
        workspace=str(tmp_path),
        visualizer=None,
        mcp_tool_provider=FailingMCPToolProvider(error),
    )

    with caplog.at_level(logging.WARNING):
        conversation._ensure_agent_ready()

    assert conversation._agent_ready
    assert any(
        "MCP server startup failed for broken" in record.message
        for record in caplog.records
    )
    conversation.close()


def _slow_fetch_server(delay: float) -> dict[str, Any]:
    """The fetch server, started after ``delay`` seconds (a first npx/uvx run)."""
    return {
        "command": sys.executable,
        "args": [
            "-c",
            f"import runpy, time; time.sleep({delay}); "
            "runpy.run_module('tests.sdk.mcp.stdio_test_server', run_name='__main__')",
        ],
        "cwd": str(REPO_ROOT),
    }


def _mcp_conversation(
    tmp_path: Path,
    servers: dict[str, Any],
    llm: LLM | None = None,
    *,
    mcp_startup_budget: float | None = None,
    mcp_tool_provider: MCPToolProvider | None = None,
) -> LocalConversation:
    return LocalConversation(
        agent=Agent(
            llm=llm or LLM(model="test-model", api_key=SecretStr("test-key")),
            tools=[],
            include_default_tools=[],
            mcp_config=coerce_mcp_config(servers),
        ),
        workspace=str(tmp_path),
        visualizer=None,
        mcp_startup_budget=mcp_startup_budget,
        mcp_tool_provider=mcp_tool_provider,
    )


def _wait_until(condition: Any, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.05)


def test_startup_budget_starts_the_agent_before_a_slow_mcp_server(
    tmp_path: Path,
) -> None:
    # A model litellm knows locally, so agent init makes no model-info requests.
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"))
    conversation = _mcp_conversation(
        tmp_path, {"slow": _slow_fetch_server(6)}, llm, mcp_startup_budget=0.5
    )

    started = time.monotonic()
    conversation._ensure_agent_ready()
    elapsed = time.monotonic() - started

    assert elapsed < 5
    assert _mcp_tool_names(conversation) == set()
    _wait_until(lambda: _mcp_tool_names(conversation))
    assert _mcp_tool_names(conversation) == {"fetch"}
    conversation.close()


class BlockedMCPToolProvider:
    """Finishes starting only when the test releases it."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.client = EmptyMCPClient()
        self.client.tools = list(
            MCPToolDefinition.create(
                mcp_types.Tool(name="late_tool", input_schema={"type": "object"}),
                cast(MCPClient, self.client),
            )
        )

    def create_tools(self, mcp_config, timeout=30.0, **kwargs) -> MCPClient:
        self.release.wait(10)
        return cast(MCPClient, self.client)


def test_late_mcp_tools_are_closed_when_the_conversation_closed_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = BlockedMCPToolProvider()
    conversation = _mcp_conversation(
        tmp_path,
        {"blocked": {"command": "unused"}},
        mcp_startup_budget=0.1,
        mcp_tool_provider=cast(MCPToolProvider, provider),
    )
    closed: list[Any] = []
    monkeypatch.setattr(
        conversation, "_close_runtime_tools", lambda tools: closed.extend(tools)
    )
    conversation._ensure_agent_ready()

    conversation.close()
    provider.release.set()
    _wait_until(lambda: closed)

    assert [tool.name for tool in closed] == ["late_tool"]
    assert _mcp_tool_names(conversation) == set()


@pytest.mark.parametrize("use_arun", [False, True])
def test_first_step_waits_for_mcp_servers_that_missed_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, use_arun: bool
) -> None:
    provider = BlockedMCPToolProvider()
    conversation = _mcp_conversation(
        tmp_path,
        {"blocked": {"command": "unused"}},
        mcp_startup_budget=0.1,
        mcp_tool_provider=cast(MCPToolProvider, provider),
    )
    tools_at_first_step: list[set[str]] = []

    def step(self, conversation, *args, **kwargs) -> None:
        tools_at_first_step.append(_mcp_tool_names(conversation))
        conversation.state.transition(
            ConversationExecutionStatus.FINISHED, reason="test"
        )

    async def astep(self, conversation, *args, **kwargs) -> None:
        step(self, conversation)

    monkeypatch.setattr(Agent, "step", step)
    monkeypatch.setattr(Agent, "astep", astep)
    conversation.send_message("hello")
    threading.Timer(0.5, provider.release.set).start()
    try:
        if use_arun:
            asyncio.run(conversation.arun())
        else:
            conversation.run()
    finally:
        conversation.close()

    assert tools_at_first_step == [{"late_tool"}]


def _mcp_tool_names(conversation: LocalConversation) -> set[str]:
    return {
        name
        for name, tool in conversation.agent.tools_map.items()
        if isinstance(tool, MCPToolDefinition)
    }


def _failed_servers(caplog: pytest.LogCaptureFixture) -> list[str]:
    return sorted(
        record.getMessage().removeprefix(STARTUP_FAILED).split(";")[0].strip()
        for record in caplog.records
        if record.getMessage().startswith(STARTUP_FAILED)
    )


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param(UNRESPONSIVE_SERVER, id="never-answers"),
        pytest.param({"command": "missing-mcp-server-command"}, id="missing-command"),
    ],
)
def test_failing_mcp_server_keeps_the_other_servers_tools(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    broken: dict[str, Any],
) -> None:
    monkeypatch.setattr(
        local_conversation, "_RUNTIME_MCP_TIMEOUT_SECS", STARTUP_TIMEOUT
    )
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="")],
                tool_calls=[
                    MessageToolCall(
                        id="call-1",
                        name="docs_fetch",
                        arguments='{"url": "https://example.com"}',
                        origin="completion",
                    )
                ],
            ),
            Message(role="assistant", content=[TextContent(text="done")]),
        ]
    )
    conversation = _mcp_conversation(
        tmp_path, {"docs": FETCH_SERVER, "broken": broken}, llm
    )

    with caplog.at_level(logging.WARNING):
        conversation.send_message("Fetch https://example.com")
        conversation.run()

    assert _mcp_tool_names(conversation) == {"docs_fetch"}
    assert _failed_servers(caplog) == ["broken"]
    observations = [
        event
        for event in conversation.state.events
        if isinstance(event, ObservationEvent) and event.tool_name == "docs_fetch"
    ]
    assert len(observations) == 1
    observation = observations[0].observation
    assert isinstance(observation, MCPToolObservation)
    assert observation.tool_name == "docs_fetch"
    assert not observation.is_error
    assert "Fetched https://example.com" in observation.text
    conversation.close()


def test_every_mcp_server_failing_still_runs_the_conversation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    llm = TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="done")])]
    )
    conversation = _mcp_conversation(
        tmp_path,
        {
            "missing": {"command": "missing-mcp-server-command"},
            "offline": {"url": "http://127.0.0.1:9/mcp"},
        },
        llm,
    )

    with caplog.at_level(logging.WARNING):
        conversation.send_message("hello")
        conversation.run()

    assert _mcp_tool_names(conversation) == set()
    assert llm.call_count == 1
    assert _failed_servers(caplog) == ["missing", "offline"]
    conversation.close()


def test_unresponsive_mcp_servers_time_out_together(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_conversation, "_RUNTIME_MCP_TIMEOUT_SECS", STARTUP_TIMEOUT
    )
    conversation = _mcp_conversation(
        tmp_path,
        {
            "docs": FETCH_SERVER,
            "stuck1": UNRESPONSIVE_SERVER,
            "stuck2": UNRESPONSIVE_SERVER,
        },
    )

    started = time.monotonic()
    with caplog.at_level(logging.WARNING):
        conversation._ensure_agent_ready()
    elapsed = time.monotonic() - started

    assert _mcp_tool_names(conversation) == {"docs_fetch"}
    assert _failed_servers(caplog) == ["stuck1", "stuck2"]
    # Waiting for the stuck servers one after another would take twice as long.
    assert elapsed < 2 * STARTUP_TIMEOUT
    conversation.close()


def test_slow_mcp_servers_start_side_by_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Started one after another these two would need over 10 seconds.
    monkeypatch.setattr(local_conversation, "_RUNTIME_MCP_TIMEOUT_SECS", 10)
    conversation = _mcp_conversation(
        tmp_path, {"slow1": _slow_fetch_server(5), "slow2": _slow_fetch_server(5)}
    )

    conversation._ensure_agent_ready()

    assert _mcp_tool_names(conversation) == {"slow1_fetch", "slow2_fetch"}
    conversation.close()


def test_single_enabled_mcp_server_keeps_unprefixed_tool_names(
    tmp_path: Path,
) -> None:
    conversation = _mcp_conversation(
        tmp_path,
        {"docs": FETCH_SERVER, "off": {"command": "unused", "enabled": False}},
    )

    conversation._ensure_agent_ready()

    assert _mcp_tool_names(conversation) == {"fetch"}
    conversation.close()
