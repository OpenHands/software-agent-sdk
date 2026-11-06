"""Tests for how LocalConversation wires MCP servers into a running agent."""

import logging
import sys
import time
from pathlib import Path
from typing import Any, cast

import mcp.types as mcp_types
import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.impl import local_conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import MCPServer, coerce_mcp_config
from openhands.sdk.mcp.definition import MCPToolObservation
from openhands.sdk.mcp.exceptions import MCPError
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
    tmp_path: Path, servers: dict[str, Any], llm: LLM | None = None
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
    )


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
        pytest.param({}, id="invalid-entry"),
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
