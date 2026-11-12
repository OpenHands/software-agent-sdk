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
    return {tool["name"] for tool in conversation.mcp_gateway.list_tools()["tools"]}


def test_replace_removes_and_closes_only_obsolete_clients(tmp_path: Path) -> None:
    initial = coerce_mcp_config({"alpha": FETCH_SERVER, "beta": FETCH_SERVER})
    conversation = conversation_with_servers(tmp_path, initial)
    try:
        conversation._ensure_agent_ready()
        alpha = conversation.mcp_gateway.clients["alpha"]
        beta = conversation.mcp_gateway.clients["beta"]
        gateway_tools = dict(conversation.agent.tools_map)
        prompt_events = list(conversation.state.events)
        revision = conversation.mcp_gateway.revision

        replacement = coerce_mcp_config({"beta": FETCH_SERVER, "gamma": FETCH_SERVER})
        conversation.update_mcp_config(replacement)

        assert conversation.agent is conversation.state.agent
        assert conversation.agent.mcp_config == replacement
        assert mcp_tool_names(conversation) == {"beta.fetch", "gamma.fetch"}
        assert conversation.mcp_gateway.clients["beta"] is beta
        assert conversation.mcp_gateway.revision != revision
        assert alpha._closed
        assert not beta._closed
        assert all(
            conversation.agent.tools_map[name] is tool
            for name, tool in gateway_tools.items()
        )
        assert list(conversation.state.events) == prompt_events

        gamma = conversation.mcp_gateway.clients["gamma"]
        conversation.update_mcp_config({})
        assert mcp_tool_names(conversation) == set()
        assert beta._closed
        assert gamma._closed
    finally:
        conversation.close()


def test_failed_replacement_rolls_back_connections_and_config(tmp_path: Path) -> None:
    initial = coerce_mcp_config({"docs": FETCH_SERVER})
    conversation = conversation_with_servers(tmp_path, initial)
    try:
        conversation._ensure_agent_ready()
        old_client = conversation.mcp_gateway.clients["docs"]
        revision = conversation.mcp_gateway.revision
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
        assert conversation.mcp_gateway.clients["docs"] is old_client
        assert conversation.mcp_gateway.revision == revision
        assert mcp_tool_names(conversation) == {"docs.fetch"}
        assert not old_client._closed
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
        assert mcp_tool_names(conversation) == {"docs.fetch"}
    finally:
        conversation.close()


def test_first_server_added_after_initialization_registers_gateway_tools(
    tmp_path: Path,
) -> None:
    conversation = conversation_with_servers(tmp_path, {})
    try:
        conversation._ensure_agent_ready()
        assert "list_mcp_tools" not in conversation.agent.tools_map
        prompt_events = list(conversation.state.events)
        conversation.update_mcp_config(coerce_mcp_config({"docs": FETCH_SERVER}))
        assert {"list_mcp_tools", "call_mcp_tool"} <= set(conversation.agent.tools_map)
        assert mcp_tool_names(conversation) == {"docs.fetch"}
        assert list(conversation.state.events) == prompt_events
        listing = conversation.mcp_gateway.list_tools()
        assert not conversation.mcp_gateway.call(
            "docs.fetch", {"url": "x"}, listing["revision"], conversation
        ).is_error
    finally:
        conversation.close()


def test_failed_first_server_leaves_gateway_tools_unregistered(tmp_path: Path) -> None:
    conversation = conversation_with_servers(tmp_path, {})
    try:
        conversation._ensure_agent_ready()
        with pytest.raises(Exception):
            conversation.update_mcp_config(
                coerce_mcp_config({"broken": {"command": "nonexistent-mcp-executable"}})
            )
        assert conversation.agent.mcp_config == {}
        assert conversation.agent is conversation.state.agent
        assert "list_mcp_tools" not in conversation.agent.tools_map
        assert conversation._mcp_gateway is None
    finally:
        conversation.close()


def test_single_server_addition_keeps_names_and_connection(tmp_path: Path) -> None:
    initial = coerce_mcp_config({"docs": FETCH_SERVER})
    conversation = conversation_with_servers(tmp_path, initial)
    try:
        conversation._ensure_agent_ready()
        old_client = conversation.mcp_gateway.clients["docs"]
        conversation.update_mcp_config(
            coerce_mcp_config({"docs": FETCH_SERVER, "other": FETCH_SERVER})
        )
        assert mcp_tool_names(conversation) == {"docs.fetch", "other.fetch"}
        assert conversation.mcp_gateway.clients["docs"] is old_client
        assert not old_client._closed
    finally:
        conversation.close()


def test_server_names_with_dots_remain_in_catalog_after_addition(
    tmp_path: Path,
) -> None:
    initial = coerce_mcp_config({"docs.search": FETCH_SERVER})
    conversation = conversation_with_servers(tmp_path, initial)
    try:
        conversation._ensure_agent_ready()
        existing = conversation.mcp_gateway.clients["docs.search"]
        conversation.update_mcp_config(
            coerce_mcp_config({"docs.search": FETCH_SERVER, "other": FETCH_SERVER})
        )
        assert conversation.mcp_gateway.clients["docs.search"] is existing
        assert mcp_tool_names(conversation) == {"docs.search.fetch", "other.fetch"}
    finally:
        conversation.close()


def test_legacy_runtime_update_is_rejected_without_rewiring_tools(
    tmp_path: Path,
) -> None:
    conversation = LocalConversation(
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("test-key")),
            tools=[],
            include_default_tools=[],
            mcp_gateway=False,
            mcp_config=coerce_mcp_config({"docs": FETCH_SERVER}),
        ),
        workspace=tmp_path,
        visualizer=None,
    )
    try:
        conversation._ensure_agent_ready()
        old_tool = conversation.agent.tools_map["fetch"]
        with pytest.raises(ValueError, match="require the MCP gateway"):
            conversation.update_mcp_config(coerce_mcp_config({"extra": FETCH_SERVER}))
        assert conversation.agent.tools_map["fetch"] is old_tool
        assert conversation.agent.mcp_config == coerce_mcp_config(
            {"docs": FETCH_SERVER}
        )
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
        client = conversation.mcp_gateway.clients["empty"]
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
        old_client = conversation.mcp_gateway.clients["docs"]
        changed = coerce_mcp_config(
            {"docs": {**FETCH_SERVER, "env": {"MCP_UPDATE_TEST": "changed"}}}
        )
        conversation.update_mcp_config(changed)
        new_client = conversation.mcp_gateway.clients["docs"]
        assert new_client is not old_client
        assert old_client._closed
        assert not new_client._closed
        assert mcp_tool_names(conversation) == {"docs.fetch"}
    finally:
        conversation.close()
