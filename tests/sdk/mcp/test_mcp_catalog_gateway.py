"""A stable MCP gateway exposes versioned schemas and dispatches real stdio tools."""

import json
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, OpenHandsAgentSettings
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.tool.builtins import BUILT_IN_TOOL_CLASSES, builtin_tool_class
from openhands.sdk.tool.builtins.mcp_gateway import CallMCPToolAction, JSONValue
from openhands.sdk.tool.registry import resolve_tool
from openhands.sdk.tool.spec import Tool


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
    filter_regex: str | None = None,
) -> LocalConversation:
    return LocalConversation(
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("test-key")),
            tools=[],
            include_default_tools=[],
            mcp_config={"alpha": SERVER, "beta": SERVER},
            mcp_gateway_startup_catalog=startup_catalog,
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


def test_gateway_arguments_have_typed_json_schema_and_accept_nested_values():
    schema = CallMCPToolAction.model_json_schema()
    assert schema["properties"]["arguments"]["additionalProperties"] is not True
    arguments: dict[str, JSONValue] = {
        "items": [{"enabled": True, "value": None, "count": 3.5}]
    }
    action = CallMCPToolAction(name="docs.fetch", arguments=arguments, revision="v1")
    assert action.arguments == arguments


def test_new_agents_and_settings_use_gateway_by_default(tmp_path: Path):
    agent = Agent(
        llm=LLM(model="test-model", api_key=SecretStr("test-key")),
        tools=[],
        include_default_tools=[],
        mcp_config={"alpha": SERVER},
    )
    assert agent.mcp_gateway is True
    assert "mcp_gateway_initial_tools" not in Agent.model_fields
    assert (
        OpenHandsAgentSettings(mcp_config={"alpha": SERVER}).create_agent().mcp_gateway
    )
    conversation = LocalConversation(
        agent=agent,
        workspace=tmp_path,
        persistence_dir=tmp_path / "history",
        delete_on_close=False,
        visualizer=None,
    )
    try:
        conversation._ensure_agent_ready()
        assert {"list_mcp_tools", "call_mcp_tool"} <= set(conversation.agent.tools_map)
        assert "fetch" not in conversation.agent.tools_map
        assert (
            json.loads(call(conversation, "list_mcp_tools", {}).text)["tools"][0][
                "name"
            ]
            == "alpha.fetch"
        )
    finally:
        conversation.close()


@pytest.mark.parametrize("remove_flag", [False, True])
def test_legacy_mcp_conversation_keeps_its_mode_on_resume(
    tmp_path: Path, remove_flag: bool
):
    first = LocalConversation(
        agent=Agent(
            llm=LLM(model="test-model", api_key=SecretStr("test-key")),
            tools=[],
            include_default_tools=[],
            mcp_config={"alpha": SERVER},
            mcp_gateway=False,
        ),
        workspace=tmp_path,
        persistence_dir=tmp_path / "history",
        delete_on_close=False,
        visualizer=None,
    )
    try:
        first._ensure_agent_ready()
        assert "fetch" in first.agent.tools_map
        assert "list_mcp_tools" not in first.agent.tools_map
        assert first.state.persistence_dir is not None
        state_path = Path(first.state.persistence_dir) / "base_state.json"
        conversation_id = first.state.id
    finally:
        first.close()

    if remove_flag:
        stored = json.loads(state_path.read_text())
        stored["agent"].pop("mcp_gateway")
        state_path.write_text(json.dumps(stored))

    resumed = LocalConversation(
        agent=None,
        workspace=tmp_path,
        persistence_dir=tmp_path / "history",
        conversation_id=conversation_id,
        delete_on_close=False,
        visualizer=None,
    )
    try:
        assert isinstance(resumed.agent, Agent)
        assert resumed.agent.mcp_gateway is False
        resumed._ensure_agent_ready()
        assert "fetch" in resumed.agent.tools_map
        assert "list_mcp_tools" not in resumed.agent.tools_map
    finally:
        resumed.close()

    runtime_agent = Agent(
        llm=LLM(model="test-model", api_key=SecretStr("test-key")),
        tools=[],
        include_default_tools=[],
        mcp_config={"alpha": SERVER},
    )
    replacement = LocalConversation(
        agent=runtime_agent,
        workspace=tmp_path,
        persistence_dir=tmp_path / "history",
        conversation_id=conversation_id,
        visualizer=None,
    )
    try:
        assert runtime_agent.mcp_gateway is True
        assert isinstance(replacement.agent, Agent)
        assert replacement.agent.mcp_gateway is False
        assert replacement.state.agent is replacement.agent
    finally:
        replacement.close()

    with pytest.raises(ValueError, match="MCP exposure mode"):
        LocalConversation(
            agent=runtime_agent.model_copy(update={"mcp_gateway": True}),
            workspace=tmp_path,
            persistence_dir=tmp_path / "history",
            conversation_id=conversation_id,
            visualizer=None,
        )


def test_gateway_tools_resolve_as_builtins():
    for class_name, tool_name in (
        ("ListMCPToolsTool", "list_mcp_tools"),
        ("CallMCPToolTool", "call_mcp_tool"),
    ):
        tool_class = BUILT_IN_TOOL_CLASSES[class_name]
        assert tool_class.__module__.startswith("openhands.sdk.tool.builtins.")
        assert builtin_tool_class(tool_name) is tool_class
        assert not tool_class.user_selectable


def test_gateway_lists_real_schemas_and_validates_calls(tmp_path: Path):
    conversation = gateway_conversation(tmp_path)
    try:
        conversation._ensure_agent_ready()
        assert {"list_mcp_tools", "call_mcp_tool"} <= set(conversation.agent.tools_map)
        assert isinstance(
            conversation.agent.tools_map["list_mcp_tools"],
            BUILT_IN_TOOL_CLASSES["ListMCPToolsTool"],
        )
        assert isinstance(
            conversation.agent.tools_map["call_mcp_tool"],
            BUILT_IN_TOOL_CLASSES["CallMCPToolTool"],
        )
        assert isinstance(
            resolve_tool(Tool(name="list_mcp_tools"), conversation.state)[0],
            BUILT_IN_TOOL_CLASSES["ListMCPToolsTool"],
        )
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
    conversation = gateway_conversation(tmp_path, startup_catalog=True)
    try:
        conversation._ensure_agent_ready()
        prompt = conversation.agent.static_system_message
        assert "<MCP_CATALOG>" in prompt
        assert '"alpha.fetch"' in prompt
        assert '"beta.fetch"' in prompt
        assert '"input_schema"' in prompt
        assert '"url"' in prompt
        assert conversation.state.mcp_gateway_selected_tools is None
    finally:
        conversation.close()


def test_gateway_filter_matches_namespaced_tools_and_cannot_be_bypassed(
    tmp_path: Path,
):
    conversation = gateway_conversation(
        tmp_path, filter_regex=r"^alpha\.fetch$", startup_catalog=True
    )
    try:
        conversation._ensure_agent_ready()
        assert {"list_mcp_tools", "call_mcp_tool"} <= set(conversation.agent.tools_map)
        prompt = conversation.agent.static_system_message
        assert '"alpha.fetch"' in prompt
        assert '"beta.fetch"' not in prompt
        listing = json.loads(call(conversation, "list_mcp_tools", {}).text)
        assert [tool["name"] for tool in listing["tools"]] == ["alpha.fetch"]
        assert (
            json.loads(call(conversation, "list_mcp_tools", {"query": "beta"}).text)[
                "tools"
            ]
            == []
        )
        blocked = call(
            conversation,
            "call_mcp_tool",
            {
                "name": "beta.fetch",
                "arguments": {"url": "x"},
                "revision": listing["revision"],
            },
        )
        assert blocked.is_error
        assert not call(
            conversation,
            "call_mcp_tool",
            {
                "name": "alpha.fetch",
                "arguments": {"url": "x"},
                "revision": listing["revision"],
            },
        ).is_error

        conversation.select_mcp_tools(["beta.fetch"])
        assert conversation.mcp_gateway.list_tools()["tools"] == []
        assert conversation.mcp_gateway.call(
            "beta.fetch", {"url": "x"}, conversation.mcp_gateway.revision, None
        ).is_error
        conversation.select_mcp_tools(None)
        assert [
            tool["name"] for tool in conversation.mcp_gateway.list_tools()["tools"]
        ] == ["alpha.fetch"]
    finally:
        conversation.close()


def test_gateway_filter_uses_prefix_match(tmp_path: Path):
    conversation = gateway_conversation(tmp_path, filter_regex="fetch")
    try:
        conversation._ensure_agent_ready()
        assert conversation.mcp_gateway.list_tools()["tools"] == []
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
    first = gateway_conversation(tmp_path, filter_regex=r"^alpha\.")
    first._ensure_agent_ready()
    first.select_mcp_tools(["beta.fetch"])
    assert first.mcp_gateway.list_tools()["tools"] == []
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
        resumed.select_mcp_tools(None)
        assert [tool["name"] for tool in resumed.mcp_gateway.list_tools()["tools"]] == [
            "alpha.fetch"
        ]
    finally:
        resumed.close()
