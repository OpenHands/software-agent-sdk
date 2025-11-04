"""
Unit tests for pause functionality.

Tests the core behavior: pause agent execution between steps.
Key requirements:
1. Multiple pause method calls successively only create one PauseEvent
2. Calling conversation.pause() while conversation.run() is still running in a
   separate thread will pause the agent
3. Calling conversation.run() on an already paused agent will resume it
"""

import threading
from collections.abc import Sequence
from typing import ClassVar
from unittest.mock import patch

import pytest
from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import (
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
)
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.conversation.base import BaseConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent, PauseEvent
from openhands.sdk.llm import (
    LLM,
    ImageContent,
    Message,
    TextContent,
)
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.sdk.tool import (
    Action,
    Observation,
    Tool,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)


class PauseFunctionalityMockAction(Action):
    """Mock action schema for testing."""

    command: str


class PauseFunctionalityMockObservation(Observation):
    """Mock observation schema for testing."""

    result: str

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=self.result)]


class BlockingExecutor(
    ToolExecutor[PauseFunctionalityMockAction, PauseFunctionalityMockObservation]
):
    def __init__(self, step_entered: threading.Event):
        self.step_entered: threading.Event = step_entered

    def __call__(
        self,
        action: PauseFunctionalityMockAction,
        conversation: BaseConversation | None = None,
    ) -> PauseFunctionalityMockObservation:
        # Signal we've entered tool execution for this step
        self.step_entered.set()
        return PauseFunctionalityMockObservation(result=f"Executed: {action.command}")


class TestExecutor(
    ToolExecutor[PauseFunctionalityMockAction, PauseFunctionalityMockObservation]
):
    """Test executor for pause functionality testing."""

    def __call__(
        self,
        action: PauseFunctionalityMockAction,
        conversation: BaseConversation | None = None,
    ) -> PauseFunctionalityMockObservation:
        return PauseFunctionalityMockObservation(result=f"Executed: {action.command}")


class PauseFunctionalityTestTool(
    ToolDefinition[PauseFunctionalityMockAction, PauseFunctionalityMockObservation]
):
    """Concrete tool for pause functionality testing."""

    name: ClassVar[str] = "test_tool"

    @classmethod
    def create(
        cls, conv_state=None, **params
    ) -> Sequence["PauseFunctionalityTestTool"]:
        return [
            cls(
                description="A test tool",
                action_type=PauseFunctionalityMockAction,
                observation_type=PauseFunctionalityMockObservation,
                executor=TestExecutor(),
            )
        ]


def _make_tool(conv_state=None, **params) -> Sequence[ToolDefinition]:
    """Factory function for creating test tools."""
    return PauseFunctionalityTestTool.create(conv_state, **params)


class BlockingTestTool(
    ToolDefinition[PauseFunctionalityMockAction, PauseFunctionalityMockObservation]
):
    """Concrete tool for blocking pause testing."""

    name: ClassVar[str] = "test_tool"

    @classmethod
    def create(
        cls, conv_state=None, step_entered=None, **params
    ) -> Sequence["BlockingTestTool"]:
        if step_entered is None:
            raise ValueError("step_entered is required for BlockingTestTool")
        return [
            cls(
                description="Blocking tool for pause test",
                action_type=PauseFunctionalityMockAction,
                observation_type=PauseFunctionalityMockObservation,
                executor=BlockingExecutor(step_entered),
            )
        ]


class TestPauseFunctionality:
    """Test suite for pause functionality."""

    def setup_method(self):
        """Set up test fixtures."""

        self.llm: LLM = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )

        register_tool("test_tool", _make_tool)

        self.agent: Agent = Agent(
            llm=self.llm,
            tools=[Tool(name="test_tool")],
        )
        self.conversation: LocalConversation = Conversation(agent=self.agent)

    def test_pause_basic_functionality(self):
        """Test basic pause operations."""
        # Test initial state
        assert (
            self.conversation.state.execution_status == ConversationExecutionStatus.IDLE
        )
        assert len(self.conversation.state.events) == 1  # System prompt event

        # Test pause method
        self.conversation.pause()
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.PAUSED
        )

        pause_events = [
            event
            for event in self.conversation.state.events
            if isinstance(event, PauseEvent)
        ]
        assert len(pause_events) == 1
        assert pause_events[0].source == "user"

    @patch("openhands.sdk.llm.llm.litellm_completion")
    def test_pause_during_normal_execution(self, mock_completion):
        """Test pausing before run() starts - pause is reset and agent runs normally."""
        # Mock LLM to return a message that finishes execution
        mock_completion.return_value = ModelResponse(
            id="response_msg",
            choices=[
                Choices(
                    message=LiteLLMMessage(role="assistant", content="Task completed")
                )
            ],
            created=0,
            model="test-model",
            object="chat.completion",
        )

        # Send message and start execution
        self.conversation.send_message(
            Message(role="user", content=[TextContent(text="Hello")])
        )

        # Pause immediately (before run starts)
        self.conversation.pause()

        # Verify pause was set
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.PAUSED
        )

        # Run resets pause flag at start and proceeds normally
        self.conversation.run()

        # Agent should be finished (pause was reset at start of run)
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.FINISHED
        )

        # Should have pause event from the pause() call
        pause_events = [
            event
            for event in self.conversation.state.events
            if isinstance(event, PauseEvent)
        ]
        assert len(pause_events) == 1

    @patch("openhands.sdk.llm.llm.litellm_completion")
    def test_resume_paused_agent(self, mock_completion):
        """Test pausing before run() - pause is reset and agent runs normally."""
        # Mock LLM to return a message that finishes execution
        mock_completion.return_value = ModelResponse(
            id="response_msg",
            choices=[
                Choices(
                    message=LiteLLMMessage(role="assistant", content="Task completed")
                )
            ],
            created=0,
            model="test-model",
            object="chat.completion",
        )

        # Send message
        self.conversation.send_message(
            Message(role="user", content=[TextContent(text="Hello")])
        )

        # Pause before run
        self.conversation.pause()
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.PAUSED
        )

        # First run() call resets pause and runs normally
        self.conversation.run()

        # Agent should be finished (pause was reset at start of run)
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.FINISHED
        )

        # Should have agent message since run completed normally
        agent_messages = [
            event
            for event in self.conversation.state.events
            if isinstance(event, MessageEvent) and event.source == "agent"
        ]
        assert len(agent_messages) == 1  # Agent ran and completed

    @patch("openhands.sdk.llm.llm.litellm_completion")
    def test_pause_with_confirmation_mode(self, mock_completion):
        """Test that pause before run() with confirmation mode - pause is reset and agent waits for confirmation."""  # noqa: E501
        # Enable confirmation mode
        self.conversation.set_confirmation_policy(AlwaysConfirm())
        self.conversation.pause()
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.PAUSED
        )

        # Mock action
        tool_call = ChatCompletionMessageToolCall(
            id="call_1",
            type="function",
            function=Function(
                name="test_tool",
                arguments='{"command": "test_command"}',
            ),
        )
        mock_completion.return_value = ModelResponse(
            id="response_action",
            choices=[
                Choices(
                    message=LiteLLMMessage(
                        role="assistant",
                        content="",
                        tool_calls=[tool_call],
                    )
                )
            ],
            created=0,
            model="test-model",
            object="chat.completion",
        )

        # Send message
        self.conversation.send_message(
            Message(role="user", content=[TextContent(text="Execute command")])
        )

        # Run resets pause and proceeds to create action, then waits for confirmation
        self.conversation.run()

        # Pause should be reset, agent should be waiting for confirmation
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
        )

        # Action did not execute (no ObservationEvent should be recorded)

        observations = [
            event
            for event in self.conversation.state.events
            if isinstance(event, ObservationEvent)
        ]
        assert len(observations) == 0

        # But there should be at least one ActionEvent pending confirmation
        action_events = [
            event
            for event in self.conversation.state.events
            if isinstance(event, ActionEvent)
        ]
        assert len(action_events) >= 1

    def test_multiple_pause_calls_create_one_event(self):
        """Test that multiple successive pause calls only create one PauseEvent."""
        # Call pause multiple times successively
        self.conversation.pause()
        self.conversation.pause()
        self.conversation.pause()

        # Should have only ONE pause event (requirement #1)
        pause_events = [
            event
            for event in self.conversation.state.events
            if isinstance(event, PauseEvent)
        ]
        assert len(pause_events) == 1, (
            f"Expected 1 PauseEvent, got {len(pause_events)}. "
            "Multiple successive pause calls should only create one PauseEvent."
        )

        # State should be paused
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.PAUSED
        )

    @pytest.mark.timeout(3)
    @patch("openhands.sdk.llm.llm.litellm_completion")
    def test_pause_while_running_continuous_actions(self, mock_completion):
        step_entered = threading.Event()

        def _make_blocking_tool(conv_state=None, **kwargs) -> Sequence[ToolDefinition]:
            return BlockingTestTool.create(
                conv_state, step_entered=step_entered, **kwargs
            )

        register_tool("test_tool", _make_blocking_tool)
        agent = Agent(
            llm=self.llm,
            tools=[Tool(name="test_tool")],
        )
        conversation = Conversation(agent=agent, stuck_detection=False)

        # Swap them in for this test only
        self.agent = agent
        self.conversation = conversation

        # LLM continuously emits actions (no finish)
        tool_call = ChatCompletionMessageToolCall(
            id="call_loop",
            type="function",
            function=Function(
                name="test_tool",
                arguments='{"command": "loop_forever"}',
            ),
        )
        import time

        def side_effect(*_args, **_kwargs):
            return ModelResponse(
                id="response_action_loop",
                choices=[
                    Choices(
                        message=LiteLLMMessage(
                            role="assistant",
                            content="I'll execute loop_forever",
                            tool_calls=[tool_call],
                        )
                    )
                ],
                created=int(time.time()),
                model="test-model",
                object="chat.completion",
            )

        mock_completion.side_effect = side_effect

        # Seed a user message
        self.conversation.send_message(
            Message(
                role="user", content=[TextContent(text="Loop actions until paused")]
            )
        )

        run_exc: list[Exception | None] = [None]
        finished = threading.Event()

        def run_agent():
            try:
                self.conversation.run()
            except Exception as e:
                run_exc[0] = e
            finally:
                finished.set()

        t = threading.Thread(target=run_agent, daemon=True)
        t.start()

        # Wait until we're *inside* tool execution of the current iteration
        assert step_entered.wait(timeout=3.0), "Agent never reached tool execution"
        self.conversation.pause()
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.PAUSED
        )

        assert finished.wait(timeout=3.0), "run() did not exit after pause"
        t.join(timeout=0.1)
        assert run_exc[0] is None, f"Run thread failed with: {run_exc[0]}"

        # paused, not finished, exactly one PauseEvent
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.PAUSED
        )
        pause_events = [
            e for e in self.conversation.state.events if isinstance(e, PauseEvent)
        ]
        assert len(pause_events) == 1, f"Expected 1 PauseEvent, got {len(pause_events)}"
