"""A stable MCP gateway exposes versioned schemas and dispatches real stdio tools."""

import json
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.mcp.config import MCPServer


REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER = MCPServer(
    command=sys.executable,
    args=["-m", "tests.sdk.mcp.stdio_test_server"],
    cwd=str(REPO_ROOT),
)


def gateway_conversation(
    tmp_path: Path,
    *,
    startup_catalog: bool = False,
    initial_tools: list[str] | None = None,
    filter_regex: str | None = None,
) -> LocalConversation:
    return LocalConversation(
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("test-key")),
            tools=[],
            include_default_tools=[],
            mcp_config={"alpha": SERVER, "beta": SERVER},
            mcp_gateway=True,
            mcp_gateway_startup_catalog=startup_catalog,
            mcp_gateway_initial_tools=initial_tools,
            filter_tools_regex=filter_regex,
        ),
        workspace=tmp_path,
        persistence_dir=tmp_path / "history",
        delete_on_close=False,
        visualizer=None,
    )


def call(conversation: LocalConversation, name: str, args: dict):
    tool = conversation.agent.tools_map[name]
    return tool(tool.action_from_arguments(args), conversation)


def test_gateway_lists_real_schemas_and_validates_calls(tmp_path: Path):
    conversation = gateway_conversation(tmp_path)
    try:
        conversation._ensure_agent_ready()
        assert {"list_mcp_tools", "call_mcp_tool"} <= set(conversation.agent.tools_map)
        assert "alpha.fetch" not in conversation.agent.tools_map
        assert "beta.fetch" not in conversation.agent.tools_map
        listing = json.loads(call(conversation, "list_mcp_tools", {}).text)
        assert {tool["name"] for tool in listing["tools"]} == {
            "alpha.fetch",
            "beta.fetch",
        }
        assert (
            listing["tools"][0]["input_schema"]["properties"]["url"]["type"] == "string"
        )
        revision = listing["revision"]
        result = call(
            conversation,
            "call_mcp_tool",
            {
                "name": "alpha.fetch",
                "arguments": {"url": "https://example.com"},
                "revision": revision,
            },
        )
        assert not result.is_error
        assert "Fetched https://example.com" in result.text
        invalid = call(
            conversation,
            "call_mcp_tool",
            {"name": "alpha.fetch", "arguments": {"url": 123}, "revision": revision},
        )
        assert invalid.is_error
        assert "123" not in invalid.text
    finally:
        clients = list(conversation.mcp_gateway.clients.values())
        conversation.close()
        assert all(client._closed for client in clients)


def test_gateway_can_include_initial_schemas_in_system_prompt(tmp_path: Path):
    conversation = gateway_conversation(
        tmp_path, startup_catalog=True, initial_tools=["alpha.fetch"]
    )
    try:
        conversation._ensure_agent_ready()
        prompt = conversation.agent.static_system_message
        assert "<MCP_CATALOG>" in prompt
        assert '"alpha.fetch"' in prompt
        assert '"beta.fetch"' not in prompt
        assert '"input_schema"' in prompt
        assert '"url"' in prompt
        assert conversation.state.mcp_gateway_selected_tools == ["alpha.fetch"]
    finally:
        conversation.close()


def test_gateway_rejects_incompatible_tool_filter(tmp_path: Path):
    conversation = gateway_conversation(tmp_path, filter_regex="^alpha")
    try:
        with pytest.raises(ValueError, match="filter_tools_regex"):
            conversation._ensure_agent_ready()
    finally:
        conversation.close()


def test_selection_changes_revision_without_registering_new_tools(tmp_path: Path):
    conversation = gateway_conversation(tmp_path)
    try:
        conversation._ensure_agent_ready()
        before = json.loads(call(conversation, "list_mcp_tools", {}).text)
        conversation.select_mcp_tools(["beta.fetch"])
        after = json.loads(call(conversation, "list_mcp_tools", {}).text)
        assert after["revision"] != before["revision"]
        assert [tool["name"] for tool in after["tools"]] == ["beta.fetch"]
        assert {"list_mcp_tools", "call_mcp_tool"} <= set(conversation.agent.tools_map)
        assert call(
            conversation,
            "call_mcp_tool",
            {
                "name": "beta.fetch",
                "arguments": {"url": "x"},
                "revision": before["revision"],
            },
        ).is_error
        assert call(
            conversation,
            "call_mcp_tool",
            {
                "name": "alpha.fetch",
                "arguments": {"url": "x"},
                "revision": after["revision"],
            },
        ).is_error
        assert not call(
            conversation,
            "call_mcp_tool",
            {
                "name": "beta.fetch",
                "arguments": {"url": "x"},
                "revision": after["revision"],
            },
        ).is_error
        with pytest.raises(ValueError, match="Unknown MCP tools"):
            conversation.select_mcp_tools(["unknown.fetch"])
        assert conversation.state.mcp_gateway_selected_tools == ["beta.fetch"]
        conversation.select_mcp_tools([])
        assert conversation.mcp_gateway.list_tools()["tools"] == []
        assert conversation.mcp_gateway.call(
            "beta.fetch", {"url": "x"}, conversation.mcp_gateway.revision, None
        ).is_error
        conversation.select_mcp_tools(None)
        assert len(conversation.mcp_gateway.list_tools()["tools"]) == 2
    finally:
        conversation.close()


def test_gateway_selection_restored_with_fresh_catalog_epoch(tmp_path: Path):
    first = gateway_conversation(tmp_path, initial_tools=["beta.fetch"])
    first._ensure_agent_ready()
    initial = first.mcp_gateway.revision
    first.select_mcp_tools(["alpha.fetch"])
    conversation_id = first.state.id
    first.close()

    resumed = LocalConversation(
        agent=None,
        workspace=tmp_path,
        persistence_dir=tmp_path / "history",
        conversation_id=conversation_id,
        delete_on_close=False,
        visualizer=None,
    )
    try:
        resumed._ensure_agent_ready()
        listing = resumed.mcp_gateway.list_tools()
        assert isinstance(resumed.agent, Agent)
        assert resumed.agent.mcp_gateway
        assert resumed.state.mcp_gateway_selected_tools == ["alpha.fetch"]
        assert [tool["name"] for tool in listing["tools"]] == ["alpha.fetch"]
        assert listing["revision"] != initial
    finally:
        resumed.close()
