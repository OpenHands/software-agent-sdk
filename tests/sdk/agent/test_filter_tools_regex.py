"""Tests for `Agent.filter_tools_regex`.

The regex must apply both to statically configured tools resolved during
`_initialize()` and to tools materialized at runtime (e.g. MCP tools) that are
registered through `add_runtime_tools()`. Built-in default tools are exempt in
both paths.
"""

import uuid
from collections.abc import Sequence
from typing import ClassVar, cast

import pytest

from openhands.sdk import LLM
from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm.message import ImageContent, TextContent
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.mcp.utils import ToolsChangedCallback
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


class _DisallowedTool(ToolDefinition[_FilterAction, _FilterObs]):
    """Name contains 'allow' but not at the start; used for anchor tests."""

    name: ClassVar[str] = "disallowed"

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["_DisallowedTool"]:
        return [
            cls(
                description="disallowed tool",
                action_type=_FilterAction,
                observation_type=_FilterObs,
                executor=_NoopExec(),
            )
        ]


class _ExemptThinkTool(ThinkTool):
    """Built-in subclass whose name does not match any test regex."""

    name = "subclassed_think"


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


def test_add_runtime_tools_mixed_batch(tmp_path):
    """One call mixing a built-in, a matching, and a non-matching tool."""
    agent = _make_agent(filter_tools_regex=r"^allowed$")
    _initialize_agent(agent, tmp_path)

    think_tool = ThinkTool.create()[0]
    agent.add_runtime_tools(
        [_AllowedTool.create()[0], _BlockedTool.create()[0], think_tool]
    )

    assert "allowed" in agent.tools_map
    assert think_tool.name in agent.tools_map
    assert "blocked" not in agent.tools_map


def test_add_runtime_tools_all_filtered_is_noop(tmp_path):
    agent = _make_agent(filter_tools_regex=r"^allowed$")
    _initialize_agent(agent, tmp_path)
    before = dict(agent.tools_map)

    agent.add_runtime_tools([_BlockedTool.create()[0]])

    assert agent.tools_map == before


def test_filter_uses_prefix_match_semantics(tmp_path):
    """`re.match` anchors at the start of the name, like `_initialize()`:
    an unanchored pattern matches a prefix but never mid-name."""
    agent = _make_agent(filter_tools_regex=r"allow")
    _initialize_agent(agent, tmp_path)

    agent.add_runtime_tools([_AllowedTool.create()[0], _DisallowedTool.create()[0]])

    assert "allowed" in agent.tools_map
    assert "disallowed" not in agent.tools_map


def test_add_runtime_tools_builtin_subclass_is_exempt(tmp_path):
    """Subclasses of built-in tools count as built-ins for the exemption."""
    agent = _make_agent(filter_tools_regex=r"^allowed$")
    _initialize_agent(agent, tmp_path)

    agent.add_runtime_tools([_ExemptThinkTool.create()[0]])

    assert "subclassed_think" in agent.tools_map


def test_add_runtime_tools_duplicate_kept_names_still_raise(tmp_path):
    """Filtering must not weaken duplicate detection for surviving tools."""
    agent = _make_agent(filter_tools_regex=r"^allowed$")
    _initialize_agent(agent, tmp_path)

    with pytest.raises(ValueError, match="Duplicate runtime tool names"):
        agent.add_runtime_tools([_AllowedTool.create()[0], _AllowedTool.create()[0]])


def test_add_runtime_tools_conflict_with_registered_tool_still_raises(tmp_path):
    agent = _make_agent(filter_tools_regex=r"^allowed$")
    _initialize_agent(agent, tmp_path)
    agent.add_runtime_tools([_AllowedTool.create()[0]])

    with pytest.raises(ValueError, match="Duplicate tool names"):
        agent.add_runtime_tools([_AllowedTool.create()[0]])


def test_add_runtime_tools_duplicate_filtered_names_do_not_raise(tmp_path):
    """Duplicates among filtered-out tools are dropped before the duplicate
    check, matching `_initialize()` which filters before validating names."""
    agent = _make_agent(filter_tools_regex=r"^allowed$")
    _initialize_agent(agent, tmp_path)

    agent.add_runtime_tools([_BlockedTool.create()[0], _BlockedTool.create()[0]])

    assert "blocked" not in agent.tools_map


class _StaticMCPClient:
    def __init__(self, tools: list[ToolDefinition]):
        self.tools = tools


class _StaticMCPToolProvider:
    """Stands in for a live MCP server advertising two tools."""

    def create_tools(
        self,
        mcp_config: dict[str, MCPServer],
        timeout: float = 30.0,
        *,
        on_tools_changed: ToolsChangedCallback | None = None,
    ) -> MCPClient:
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
