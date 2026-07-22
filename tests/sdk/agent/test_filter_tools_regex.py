"""Tests for `Agent.filter_tools_regex`.

The regex must apply both to statically configured tools resolved during
`_initialize()` and to tools materialized at runtime (e.g. MCP tools) that are
registered through `add_runtime_tools()`. Built-in default tools are exempt in
both paths.

Regression tests for https://github.com/OpenHands/software-agent-sdk/issues/4184
"""

import uuid
from collections.abc import Sequence
from typing import ClassVar, cast

from openhands.sdk import LLM
from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm.message import ImageContent, TextContent
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.tool import ToolDefinition
from openhands.sdk.tool.builtins import ThinkTool
from openhands.sdk.tool.registry import register_tool
from openhands.sdk.tool.spec import Tool
from openhands.sdk.tool.tool import Action, Observation, ToolExecutor
from openhands.sdk.workspace import LocalWorkspace


class _FilterAction(Action):
    text: str = ""


class _FilterObs(Observation):
    out: str = ""

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=self.out)]


class _NoopExec(ToolExecutor[_FilterAction, _FilterObs]):
    def __call__(self, action: _FilterAction, conversation=None) -> _FilterObs:
        return _FilterObs(out="ok")


class _AllowedTool(ToolDefinition[_FilterAction, _FilterObs]):
    name: ClassVar[str] = "allowed"

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["_AllowedTool"]:
        return [
            cls(
                description="allowed tool",
                action_type=_FilterAction,
                observation_type=_FilterObs,
                executor=_NoopExec(),
            )
        ]


class _BlockedTool(ToolDefinition[_FilterAction, _FilterObs]):
    name: ClassVar[str] = "blocked"

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["_BlockedTool"]:
        return [
            cls(
                description="blocked tool",
                action_type=_FilterAction,
                observation_type=_FilterObs,
                executor=_NoopExec(),
            )
        ]


def _make_agent(**agent_kwargs) -> Agent:
    return Agent(
        llm=LLM(model="test-model", usage_id="test-llm"),
        tools=[],
        include_default_tools=[],
        **agent_kwargs,
    )


def _initialize_agent(agent: Agent, tmp_path) -> None:
    state = ConversationState.create(
        id=uuid.uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
    )
    agent._initialize(state)


def test_add_runtime_tools_applies_filter_tools_regex(tmp_path):
    agent = _make_agent(filter_tools_regex=r"^allowed$")
    _initialize_agent(agent, tmp_path)

    agent.add_runtime_tools([_AllowedTool.create()[0], _BlockedTool.create()[0]])

    assert "allowed" in agent.tools_map
    assert "blocked" not in agent.tools_map


def test_add_runtime_tools_keeps_builtin_tools(tmp_path):
    """Built-in default tools bypass the regex, matching `_initialize()`."""
    agent = _make_agent(filter_tools_regex=r"^allowed$")
    _initialize_agent(agent, tmp_path)

    think_tool = ThinkTool.create()[0]
    agent.add_runtime_tools([think_tool])

    assert think_tool.name in agent.tools_map


def test_add_runtime_tools_without_filter_keeps_all(tmp_path):
    agent = _make_agent()
    _initialize_agent(agent, tmp_path)

    agent.add_runtime_tools([_AllowedTool.create()[0], _BlockedTool.create()[0]])

    assert "allowed" in agent.tools_map
    assert "blocked" in agent.tools_map


def test_static_tools_apply_filter_tools_regex(tmp_path):
    register_tool("allowed", _AllowedTool)
    register_tool("blocked", _BlockedTool)
    agent = Agent(
        llm=LLM(model="test-model", usage_id="test-llm"),
        tools=[Tool(name="allowed"), Tool(name="blocked")],
        include_default_tools=[],
        filter_tools_regex=r"^allowed$",
    )
    _initialize_agent(agent, tmp_path)

    assert "allowed" in agent.tools_map
    assert "blocked" not in agent.tools_map


class _StaticMCPClient:
    def __init__(self, tools: list[ToolDefinition]):
        self.tools = tools


class _StaticMCPToolProvider:
    """Stands in for a live MCP server advertising two tools."""

    def create_tools(self, mcp_config, timeout: float = 30.0) -> MCPClient:
        return cast(
            MCPClient,
            _StaticMCPClient([_AllowedTool.create()[0], _BlockedTool.create()[0]]),
        )


def test_runtime_mcp_tools_apply_filter_tools_regex(tmp_path):
    """End-to-end through `LocalConversation._ensure_agent_ready()`."""
    agent = _make_agent(
        filter_tools_regex=r"^allowed$",
        mcp_config={"fake": {"command": "true", "args": []}},
    )
    conversation = LocalConversation(
        agent=agent,
        workspace=str(tmp_path),
        visualizer=None,
        mcp_tool_provider=_StaticMCPToolProvider(),
    )
    conversation._ensure_agent_ready()

    assert "allowed" in conversation.agent.tools_map
    assert "blocked" not in conversation.agent.tools_map
