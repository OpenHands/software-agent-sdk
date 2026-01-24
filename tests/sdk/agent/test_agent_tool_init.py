from collections.abc import Sequence
from typing import ClassVar
from unittest.mock import patch

from pydantic import Field
from rich.text import Text

from openhands.sdk import LLM, Conversation
from openhands.sdk.agent import Agent
from openhands.sdk.llm.message import ImageContent, TextContent
from openhands.sdk.tool import ToolDefinition
from openhands.sdk.tool.registry import register_tool
from openhands.sdk.tool.spec import Tool
from openhands.sdk.tool.tool import Action, Observation, ToolExecutor


class _Action(Action):
    text: str


class _Obs(Observation):
    out: str

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=self.out)]


class _Exec(ToolExecutor[_Action, _Obs]):
    def __call__(self, action: _Action, conversation=None) -> _Obs:
        return _Obs(out=action.text.upper())


class _UpperTool(ToolDefinition[_Action, _Obs]):
    """Concrete tool for uppercase testing."""

    name: ClassVar[str] = "upper"

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["_UpperTool"]:
        return [
            cls(
                description="Uppercase",
                action_type=_Action,
                observation_type=_Obs,
                executor=_Exec(),
            )
        ]


def _make_tool(conv_state=None, **kwargs) -> Sequence[ToolDefinition]:
    return _UpperTool.create(conv_state, **kwargs)


def test_agent_initializes_tools_from_toolspec_locally(monkeypatch):
    # Register a simple local tool via registry
    register_tool("upper", _make_tool)

    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[Tool(name="upper")])

    # Build a conversation; agent init is lazy (deferred to first run/send_message)
    conv = Conversation(agent=agent, visualizer=None)

    # Trigger agent initialization by calling _ensure_agent_ready()
    # This is needed because agent.tools_map requires initialization
    conv._ensure_agent_ready()

    # Access the agent's runtime tools via a small shim
    # (We don't rely on private internals; we verify init_state produced a system prompt
    # with tools included by checking that agent.step can access tools without error.)
    with patch.object(Agent, "step", wraps=agent.step):
        runtime_tools = agent.tools_map
        assert "upper" in runtime_tools
        assert "finish" in runtime_tools
        assert "think" in runtime_tools


def test_agent_include_only_finish_tool():
    """Test that only the finish tool can be included (think tool excluded)."""
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[], include_default_tools=["FinishTool"])

    conv = Conversation(agent=agent, visualizer=None)
    # Trigger lazy agent initialization
    conv._ensure_agent_ready()

    with patch.object(Agent, "step", wraps=agent.step):
        runtime_tools = agent.tools_map
        assert "finish" in runtime_tools
        assert "think" not in runtime_tools


def test_agent_include_only_think_tool():
    """Test that only the think tool can be included (finish tool excluded)."""
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[], include_default_tools=["ThinkTool"])

    conv = Conversation(agent=agent, visualizer=None)
    # Trigger lazy agent initialization
    conv._ensure_agent_ready()

    with patch.object(Agent, "step", wraps=agent.step):
        runtime_tools = agent.tools_map
        assert "finish" not in runtime_tools
        assert "think" in runtime_tools


def test_agent_disable_all_default_tools():
    """Test that all default tools can be disabled with include_default_tools=[]."""
    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[], include_default_tools=[])

    conv = Conversation(agent=agent, visualizer=None)
    # Trigger lazy agent initialization
    conv._ensure_agent_ready()

    with patch.object(Agent, "step", wraps=agent.step):
        runtime_tools = agent.tools_map
        assert "finish" not in runtime_tools
        assert "think" not in runtime_tools


# Custom finish tool for testing replacement
class _CustomFinishAction(Action):
    result: str = Field(description="The result of the task.")
    success: bool = Field(description="Whether the task was successful.")

    @property
    def visualize(self) -> Text:
        return Text(f"Custom Finish: {self.result} (success={self.success})")


class _CustomFinishObs(Observation):
    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text="Task completed.")]


class _CustomFinishExec(ToolExecutor[_CustomFinishAction, _CustomFinishObs]):
    def __call__(
        self, action: _CustomFinishAction, conversation=None
    ) -> _CustomFinishObs:
        return _CustomFinishObs.from_text(text="Task completed.")


class _CustomFinishTool(ToolDefinition[_CustomFinishAction, _CustomFinishObs]):
    """Custom finish tool with structured output."""

    name: ClassVar[str] = "finish"

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["_CustomFinishTool"]:
        return [
            cls(
                description="Custom finish tool with structured output.",
                action_type=_CustomFinishAction,
                observation_type=_CustomFinishObs,
                executor=_CustomFinishExec(),
            )
        ]


def _make_custom_finish_tool(conv_state=None, **kwargs) -> Sequence[ToolDefinition]:
    return _CustomFinishTool.create(conv_state, **kwargs)


def test_agent_replace_finish_with_custom_tool():
    """Test that the finish tool can be replaced with a custom implementation."""
    register_tool("custom_finish", _make_custom_finish_tool)

    llm = LLM(model="test-model", usage_id="test-llm")
    agent = Agent(
        llm=llm,
        tools=[Tool(name="custom_finish")],
        include_default_tools=[
            "ThinkTool"
        ],  # Only include ThinkTool, exclude FinishTool
    )

    conv = Conversation(agent=agent, visualizer=None)
    # Trigger lazy agent initialization
    conv._ensure_agent_ready()

    with patch.object(Agent, "step", wraps=agent.step):
        runtime_tools = agent.tools_map
        # Custom finish tool should be present with the name "finish"
        assert "finish" in runtime_tools
        # Verify it's our custom tool by checking the action type
        finish_tool = runtime_tools["finish"]
        assert finish_tool.action_type == _CustomFinishAction
        # Think tool should still be present
        assert "think" in runtime_tools
