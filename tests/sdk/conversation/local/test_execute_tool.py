"""Tests for conversation.execute_tool() functionality."""

import pytest
from pydantic import SecretStr

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.conversation.types import (
    ConversationCallbackType,
    ConversationTokenCallbackType,
)
from openhands.sdk.event.llm_convertible import MessageEvent, SystemPromptEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.tool import (
    Action,
    Observation,
    Tool,
    ToolDefinition,
    ToolExecutor,
    register_tool as register_tool_public,
    registry as tool_registry,
)


# Define a simple test action and observation
class ExecuteToolTestAction(Action):
    """Test action for execute_tool tests."""

    value: str = "test"


class ExecuteToolTestObservation(Observation):
    """Test observation for execute_tool tests."""

    result: str = ""


# Define a simple test tool executor
class ExecuteToolTestExecutor(
    ToolExecutor[ExecuteToolTestAction, ExecuteToolTestObservation]
):
    """Test executor that returns a simple observation."""

    def __init__(self, prefix: str = "executed"):
        self.prefix = prefix
        self.call_count = 0

    def __call__(
        self,
        action: ExecuteToolTestAction,
        conversation: "LocalConversation | None" = None,
    ) -> ExecuteToolTestObservation:
        self.call_count += 1
        return ExecuteToolTestObservation.from_text(
            f"{self.prefix}: {action.value}", result=f"{self.prefix}_{action.value}"
        )


# Define a simple test tool
class ExecuteToolTestTool(
    ToolDefinition[ExecuteToolTestAction, ExecuteToolTestObservation]
):
    """Test tool for execute_tool tests."""

    @classmethod
    def create(cls, conv_state=None, **params):
        executor = ExecuteToolTestExecutor(prefix=params.get("prefix", "executed"))
        return [
            cls(
                description="A test tool for testing execute_tool",
                action_type=ExecuteToolTestAction,
                observation_type=ExecuteToolTestObservation,
                executor=executor,
            )
        ]


@pytest.fixture(autouse=True)
def _tool_registry_snapshot():
    registry_snapshot = dict(tool_registry._REG)
    module_snapshot = dict(tool_registry._MODULE_QUALNAMES)
    register_tool_public(ExecuteToolTestTool.name, ExecuteToolTestTool)
    try:
        yield
    finally:
        tool_registry._REG.clear()
        tool_registry._REG.update(registry_snapshot)
        tool_registry._MODULE_QUALNAMES.clear()
        tool_registry._MODULE_QUALNAMES.update(module_snapshot)


class ExecuteToolDummyAgent(AgentBase):
    """Dummy agent for testing execute_tool."""

    def __init__(self, tools=None):
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        super().__init__(llm=llm, tools=tools or [])

    def init_state(
        self, state: ConversationState, on_event: ConversationCallbackType
    ) -> None:
        # Call parent init_state to properly initialize tools
        super().init_state(state, on_event)
        # Then emit the system prompt event
        event = SystemPromptEvent(
            source="agent", system_prompt=TextContent(text="dummy"), tools=[]
        )
        on_event(event)

    def step(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ) -> None:
        on_event(
            MessageEvent(
                source="agent",
                llm_message=Message(role="assistant", content=[TextContent(text="ok")]),
            )
        )


def test_execute_tool_basic():
    """Test basic execute_tool functionality."""
    agent = ExecuteToolDummyAgent(
        tools=[Tool(name="execute_tool_test", params={"prefix": "hello"})]
    )
    conversation = Conversation(agent=agent)

    # Execute the tool before run()
    action = ExecuteToolTestAction(value="world")
    result = conversation.execute_tool("execute_tool_test", action)

    # Verify the result
    assert isinstance(result, ExecuteToolTestObservation)
    assert result.result == "hello_world"
    assert "hello: world" in result.text


def test_execute_tool_initializes_agent():
    """Test that execute_tool initializes the agent if not already initialized."""
    agent = ExecuteToolDummyAgent(tools=[Tool(name="execute_tool_test", params={})])
    conversation = Conversation(agent=agent)

    # Agent should not be initialized yet
    assert not conversation._agent_ready

    # Execute the tool
    action = ExecuteToolTestAction(value="test")
    conversation.execute_tool("execute_tool_test", action)

    # Agent should now be initialized
    assert conversation._agent_ready


def test_execute_tool_before_send_message():
    """Test that execute_tool works before send_message is called."""
    agent = ExecuteToolDummyAgent(tools=[Tool(name="execute_tool_test", params={})])
    conversation = Conversation(agent=agent)

    # Execute tool before any messages
    action = ExecuteToolTestAction(value="pre-message")
    result = conversation.execute_tool("execute_tool_test", action)

    assert isinstance(result, ExecuteToolTestObservation)
    assert result.result == "executed_pre-message"

    # Now send a message - should still work
    conversation.send_message("Hello")
    assert len(conversation.state.events) >= 2  # System prompt + user message


def test_execute_tool_after_send_message():
    """Test that execute_tool works after send_message is called."""
    agent = ExecuteToolDummyAgent(tools=[Tool(name="execute_tool_test", params={})])
    conversation = Conversation(agent=agent)

    # Send a message first
    conversation.send_message("Hello")

    # Execute tool after message
    action = ExecuteToolTestAction(value="post-message")
    result = conversation.execute_tool("execute_tool_test", action)

    assert isinstance(result, ExecuteToolTestObservation)
    assert result.result == "executed_post-message"


def test_execute_tool_not_found():
    """Test that execute_tool raises KeyError for non-existent tools."""
    agent = ExecuteToolDummyAgent(tools=[Tool(name="execute_tool_test", params={})])
    conversation = Conversation(agent=agent)

    action = ExecuteToolTestAction(value="test")

    with pytest.raises(KeyError) as exc_info:
        conversation.execute_tool("nonexistent_tool", action)

    assert "nonexistent_tool" in str(exc_info.value)
    assert "not found" in str(exc_info.value)


def test_execute_tool_multiple_calls():
    """Test that execute_tool can be called multiple times."""
    agent = ExecuteToolDummyAgent(tools=[Tool(name="execute_tool_test", params={})])
    conversation = Conversation(agent=agent)

    # Execute multiple times
    for i in range(3):
        action = ExecuteToolTestAction(value=f"call_{i}")
        result = conversation.execute_tool("execute_tool_test", action)
        assert isinstance(result, ExecuteToolTestObservation)
        assert result.result == f"executed_call_{i}"


def test_execute_tool_with_conversation_context():
    """Test that execute_tool passes conversation context to the executor."""

    class ContextAwareExecutor(
        ToolExecutor[ExecuteToolTestAction, ExecuteToolTestObservation]
    ):
        """Executor that uses conversation context."""

        def __call__(
            self,
            action: ExecuteToolTestAction,
            conversation: "LocalConversation | None" = None,
        ) -> ExecuteToolTestObservation:
            # Verify conversation is passed
            conv_id = str(conversation.id) if conversation else "no_conversation"
            return ExecuteToolTestObservation.from_text(
                f"conv_id: {conv_id}", result=f"context_{action.value}"
            )

    class ContextAwareTool(
        ToolDefinition[ExecuteToolTestAction, ExecuteToolTestObservation]
    ):
        @classmethod
        def create(cls, conv_state=None, **params):
            return [
                cls(
                    description="Context-aware test tool",
                    action_type=ExecuteToolTestAction,
                    observation_type=ExecuteToolTestObservation,
                    executor=ContextAwareExecutor(),
                )
            ]

    register_tool_public("context_aware", ContextAwareTool)

    agent = ExecuteToolDummyAgent(tools=[Tool(name="context_aware", params={})])
    conversation = Conversation(agent=agent)

    action = ExecuteToolTestAction(value="test")
    result = conversation.execute_tool("context_aware", action)

    # Verify conversation was passed (result should contain conversation ID)
    assert "conv_id:" in result.text
    assert isinstance(result, ExecuteToolTestObservation)
    assert result.result == "context_test"
