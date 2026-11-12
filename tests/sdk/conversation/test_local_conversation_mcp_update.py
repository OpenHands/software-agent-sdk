"""Runtime MCP server replacement uses real local stdio connections."""

import sys
import threading
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import Event, MessageEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.mcp.config import MCPServer, coerce_mcp_config
from openhands.sdk.mcp.tool import MCPToolDefinition, MCPToolExecutor
from openhands.sdk.testing import TestLLM


REPO_ROOT = Path(__file__).resolve().parents[3]
FETCH_SERVER = {
    "command": sys.executable,
    "args": ["-m", "tests.sdk.mcp.stdio_test_server"],
    "cwd": str(REPO_ROOT),
}


def conversation_with_servers(
    tmp_path: Path, servers: dict[str, MCPServer]
) -> LocalConversation:
    return LocalConversation(
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("test-key")),
            tools=[],
            include_default_tools=[],
            mcp_config=servers,
        ),
        workspace=tmp_path,
        visualizer=None,
    )


def mcp_tool_names(conversation: LocalConversation) -> set[str]:
    return {
        name
        for name, tool in conversation.agent.tools_map.items()
        if isinstance(tool, MCPToolDefinition)
    }


def test_replace_removes_and_closes_only_obsolete_clients(tmp_path: Path) -> None:
    initial = coerce_mcp_config({"alpha": FETCH_SERVER, "beta": FETCH_SERVER})
    conversation = conversation_with_servers(tmp_path, initial)
    try:
        conversation._ensure_agent_ready()
        alpha = conversation.agent.tools_map["alpha_fetch"]
        beta = conversation.agent.tools_map["beta_fetch"]
        assert isinstance(alpha, MCPToolDefinition)
        assert isinstance(beta, MCPToolDefinition)
        assert isinstance(alpha.executor, MCPToolExecutor)
        assert isinstance(beta.executor, MCPToolExecutor)
        prompt_events = list(conversation.state.events)

        replacement = coerce_mcp_config({"beta": FETCH_SERVER, "gamma": FETCH_SERVER})
        conversation.update_mcp_config(replacement)

        assert conversation.agent is conversation.state.agent
        assert conversation.agent.mcp_config == replacement
        assert mcp_tool_names(conversation) == {"beta_fetch", "gamma_fetch"}
        assert conversation.agent.tools_map["beta_fetch"].executor is beta.executor
        assert alpha.executor.client._closed
        assert not beta.executor.client._closed
        assert list(conversation.state.events) == prompt_events

        gamma = conversation.agent.tools_map["gamma_fetch"]
        assert isinstance(gamma, MCPToolDefinition)
        assert isinstance(gamma.executor, MCPToolExecutor)
        conversation.update_mcp_config({})
        assert mcp_tool_names(conversation) == set()
        assert beta.executor.client._closed
        assert gamma.executor.client._closed
    finally:
        conversation.close()


def test_failed_replacement_rolls_back_connections_and_config(tmp_path: Path) -> None:
    initial = coerce_mcp_config({"docs": FETCH_SERVER})
    conversation = conversation_with_servers(tmp_path, initial)
    try:
        conversation._ensure_agent_ready()
        old_tool = conversation.agent.tools_map["fetch"]
        assert isinstance(old_tool, MCPToolDefinition)
        assert isinstance(old_tool.executor, MCPToolExecutor)
        with pytest.raises(Exception):
            conversation.update_mcp_config(
                coerce_mcp_config(
                    {
                        "docs": FETCH_SERVER,
                        "broken": {"command": "nonexistent-mcp-executable"},
                    }
                )
            )
        assert conversation.agent is conversation.state.agent
        assert conversation.agent.mcp_config == initial
        assert conversation.agent.tools_map["fetch"].executor is old_tool.executor
        assert not old_tool.executor.client._closed
    finally:
        conversation.close()


def test_update_before_initialization_defers_connection(tmp_path: Path) -> None:
    conversation = conversation_with_servers(tmp_path, {})
    replacement = coerce_mcp_config({"docs": FETCH_SERVER})
    try:
        conversation.update_mcp_config(replacement)
        assert conversation.agent is conversation.state.agent
        assert conversation.agent.mcp_config == replacement
        assert not conversation._agent_ready
        conversation._ensure_agent_ready()
        assert mcp_tool_names(conversation) == {"fetch"}
    finally:
        conversation.close()


def test_single_server_addition_renames_existing_tools(tmp_path: Path) -> None:
    initial = coerce_mcp_config({"docs": FETCH_SERVER})
    conversation = conversation_with_servers(tmp_path, initial)
    try:
        conversation._ensure_agent_ready()
        old_tool = conversation.agent.tools_map["fetch"]
        assert isinstance(old_tool, MCPToolDefinition)
        assert isinstance(old_tool.executor, MCPToolExecutor)
        conversation.update_mcp_config(
            coerce_mcp_config({"docs": FETCH_SERVER, "other": FETCH_SERVER})
        )
        assert mcp_tool_names(conversation) == {"docs_fetch", "other_fetch"}
        assert old_tool.executor.client._closed
    finally:
        conversation.close()


def test_update_rejects_in_flight_agent_step(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    llm = TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="done")])]
    )

    def pause_agent_message(event: Event) -> None:
        if isinstance(event, MessageEvent) and event.source == "agent":
            entered.set()
            assert release.wait(10)

    conversation = LocalConversation(
        agent=Agent(llm=llm, tools=[], include_default_tools=[]),
        workspace=tmp_path,
        visualizer=None,
        callbacks=[pause_agent_message],
    )
    runner = threading.Thread(target=conversation.run)
    try:
        conversation.send_message("hello")
        runner.start()
        assert entered.wait(10)
        with pytest.raises(RuntimeError, match="during an agent step"):
            conversation.update_mcp_config(coerce_mcp_config({"docs": FETCH_SERVER}))
        assert conversation.agent.mcp_config == {}
    finally:
        release.set()
        runner.join(timeout=10)
        conversation.close()
    assert not runner.is_alive()
    assert llm.call_count == 1


def test_server_with_no_tools_is_closed_when_removed(tmp_path: Path) -> None:
    empty = coerce_mcp_config(
        {
            "empty": {
                "command": sys.executable,
                "args": [
                    "-c",
                    "from fastmcp import FastMCP; "
                    "FastMCP('empty').run(transport='stdio', show_banner=False)",
                ],
            }
        }
    )
    conversation = conversation_with_servers(tmp_path, empty)
    try:
        conversation._ensure_agent_ready()
        assert mcp_tool_names(conversation) == set()
        client = conversation._mcp_clients["empty"]
        conversation.update_mcp_config({})
        assert client._closed
        assert mcp_tool_names(conversation) == set()
    finally:
        conversation.close()


def test_replacing_same_named_server_closes_previous_client(tmp_path: Path) -> None:
    conversation = conversation_with_servers(
        tmp_path, coerce_mcp_config({"docs": FETCH_SERVER})
    )
    try:
        conversation._ensure_agent_ready()
        old_tool = conversation.agent.tools_map["fetch"]
        assert isinstance(old_tool, MCPToolDefinition)
        assert isinstance(old_tool.executor, MCPToolExecutor)
        changed = coerce_mcp_config(
            {"docs": {**FETCH_SERVER, "env": {"MCP_UPDATE_TEST": "changed"}}}
        )
        conversation.update_mcp_config(changed)
        new_tool = conversation.agent.tools_map["fetch"]
        assert isinstance(new_tool, MCPToolDefinition)
        assert isinstance(new_tool.executor, MCPToolExecutor)
        assert new_tool.executor.client is not old_tool.executor.client
        assert old_tool.executor.client._closed
        assert not new_tool.executor.client._closed
    finally:
        conversation.close()
