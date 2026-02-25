"""
Unit tests for agent status transitions.

Tests that the agent correctly transitions between execution states,
particularly focusing on transitions to RUNNING status when run() is called.

This addresses the fix for issue #865 where the agent status was not transitioning
to RUNNING when run() was called from IDLE state.

State transition matrix tested:
- IDLE -> RUNNING (when run() is called)
- PAUSED -> RUNNING (when run() is called after pause)
- WAITING_FOR_CONFIRMATION -> RUNNING (when run() is called to confirm)
- FINISHED -> IDLE -> RUNNING (when new message sent after completion)
- FINISHED/STUCK -> remain unchanged (run() exits immediately)
"""

import threading
from collections.abc import Sequence
from typing import ClassVar

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import ImageContent, Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import (
    Action,
    Observation,
    Tool,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)


class StatusTransitionMockAction(Action):
    """Mock action schema for testing."""

    command: str


class StatusTransitionMockObservation(Observation):
    """Mock observation schema for testing."""

    result: str

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=self.result)]


class StatusCheckingExecutor(
    ToolExecutor[StatusTransitionMockAction, StatusTransitionMockObservation]
):
    """Executor that captures the agent status when executed."""

    def __init__(self, status_during_execution: list[ConversationExecutionStatus]):
        self.status_during_execution: list[ConversationExecutionStatus] = (
            status_during_execution
        )

    def __call__(
        self, action: StatusTransitionMockAction, conversation=None
    ) -> StatusTransitionMockObservation:
        # Capture the agent status during execution
        if conversation:
            self.status_during_execution.append(conversation.state.execution_status)
        return StatusTransitionMockObservation(result=f"Executed: {action.command}")


class StatusTransitionTestTool(
    ToolDefinition[StatusTransitionMockAction, StatusTransitionMockObservation]
):
    """Concrete tool for status transition testing."""

    name: ClassVar[str] = "test_tool"

    @classmethod
    def create(
        cls, conv_state=None, *, executor: ToolExecutor, **params
    ) -> Sequence["StatusTransitionTestTool"]:
        return [
            cls(
                description="A test tool",
                action_type=StatusTransitionMockAction,
                observation_type=StatusTransitionMockObservation,
                executor=executor,
            )
        ]


def test_execution_status_transitions_to_running_from_idle():
    """Test that agent status transitions to RUNNING when run() is called from IDLE."""
    status_during_execution: list[ConversationExecutionStatus] = []

    def _make_tool(conv_state=None, **params) -> Sequence[ToolDefinition]:
        return StatusTransitionTestTool.create(
            executor=StatusCheckingExecutor(status_during_execution)
        )

    register_tool("test_tool", _make_tool)

    # Use TestLLM with a scripted response
    llm = TestLLM.from_messages(
        [
            Message(role="assistant", content=[TextContent(text="Task completed")]),
        ]
    )
    agent = Agent(llm=llm, tools=[])
    conversation = Conversation(agent=agent)

    # Verify initial state is IDLE
    assert conversation.state.execution_status == ConversationExecutionStatus.IDLE

    # Send message and run
    conversation.send_message(Message(role="user", content=[TextContent(text="Hello")]))
    conversation.run()

    # After run completes, status should be FINISHED
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED

    # Verify we have agent response
    agent_messages = [
        event
        for event in conversation.state.events
        if isinstance(event, MessageEvent) and event.source == "agent"
    ]
    assert len(agent_messages) == 1


def test_execution_status_is_running_during_execution_from_idle():
    """Test that agent status is RUNNING during execution when started from IDLE."""
    status_during_execution: list[ConversationExecutionStatus] = []
    execution_started = threading.Event()

    class SignalingExecutor(
        ToolExecutor[StatusTransitionMockAction, StatusTransitionMockObservation]
    ):
        """Executor that signals when execution starts and captures status."""

        def __call__(
            self, action: StatusTransitionMockAction, conversation=None
        ) -> StatusTransitionMockObservation:
            # Signal that execution has started
            execution_started.set()
            # Capture the agent status during execution
            if conversation:
                status_during_execution.append(conversation.state.execution_status)
            return StatusTransitionMockObservation(result=f"Executed: {action.command}")

    def _make_tool(conv_state=None, **params) -> Sequence[ToolDefinition]:
        return StatusTransitionTestTool.create(executor=SignalingExecutor())

    register_tool("test_tool", _make_tool)

    # Use TestLLM with scripted responses: first a tool call, then completion
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="")],
                tool_calls=[
                    MessageToolCall(
                        id="call_1",
                        name="test_tool",
                        arguments='{"command": "test_command"}',
                        origin="completion",
                    )
                ],
            ),
            Message(role="assistant", content=[TextContent(text="Task completed")]),
        ]
    )
    agent = Agent(
        llm=llm,
        tools=[Tool(name="test_tool")],
    )
    conversation = Conversation(agent=agent)

    # Verify initial state is IDLE
    assert conversation.state.execution_status == ConversationExecutionStatus.IDLE

    # Send message
    conversation.send_message(
        Message(role="user", content=[TextContent(text="Execute command")])
    )

    # Run in a separate thread so we can check status during execution
    run_complete = threading.Event()
    status_during_run: list[ConversationExecutionStatus | None] = [None]

    def run_agent():
        conversation.run()
        run_complete.set()

    t = threading.Thread(target=run_agent, daemon=True)
    t.start()

    # Wait for execution to start
    assert execution_started.wait(timeout=2.0), "Execution never started"

    # Check status while running
    status_during_run[0] = conversation.state.execution_status

    # Wait for run to complete
    assert run_complete.wait(timeout=2.0), "Run did not complete"
    t.join(timeout=0.1)

    # Verify status was RUNNING during execution
    assert status_during_run[0] == ConversationExecutionStatus.RUNNING, (
        f"Expected RUNNING status during execution, got {status_during_run[0]}"
    )

    # After run completes, status should be FINISHED
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED


def test_execution_status_transitions_to_running_from_paused():
    """Test that agent status transitions to RUNNING when run() is called from
    PAUSED."""
    # Use TestLLM with a scripted response
    llm = TestLLM.from_messages(
        [
            Message(role="assistant", content=[TextContent(text="Task completed")]),
        ]
    )
    agent = Agent(llm=llm, tools=[])
    conversation = Conversation(agent=agent)

    # Pause the conversation
    conversation.pause()
    assert conversation.state.execution_status == ConversationExecutionStatus.PAUSED

    # Send message and run
    conversation.send_message(Message(role="user", content=[TextContent(text="Hello")]))
    conversation.run()

    # After run completes, status should be FINISHED
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED

    # Verify we have agent response
    agent_messages = [
        event
        for event in conversation.state.events
        if isinstance(event, MessageEvent) and event.source == "agent"
    ]
    assert len(agent_messages) == 1


def test_execution_status_transitions_from_waiting_for_confirmation():
    """Test WAITING_FOR_CONFIRMATION -> RUNNING transition when run() is called."""
    from openhands.sdk.security.confirmation_policy import AlwaysConfirm

    def _make_tool(conv_state=None, **params) -> Sequence[ToolDefinition]:
        return StatusTransitionTestTool.create(executor=StatusCheckingExecutor([]))

    register_tool("test_tool", _make_tool)

    # Use TestLLM with scripted responses: first a tool call, then completion
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="")],
                tool_calls=[
                    MessageToolCall(
                        id="call_1",
                        name="test_tool",
                        arguments='{"command": "test_command"}',
                        origin="completion",
                    )
                ],
            ),
            Message(role="assistant", content=[TextContent(text="Task completed")]),
        ]
    )

    agent = Agent(llm=llm, tools=[Tool(name="test_tool")])
    conversation = Conversation(agent=agent)
    conversation.set_confirmation_policy(AlwaysConfirm())

    # Send message and run - should stop at WAITING_FOR_CONFIRMATION
    conversation.send_message(
        Message(role="user", content=[TextContent(text="Execute command")])
    )
    conversation.run()

    # Should be waiting for confirmation
    assert (
        conversation.state.execution_status
        == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )

    # Call run again - this confirms and should transition to RUNNING, then FINISHED
    conversation.run()

    # After confirmation and execution, should be FINISHED
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED


def test_execution_status_finished_to_idle_to_running():
    """Test FINISHED -> IDLE -> RUNNING transition when new message is sent."""
    # Use TestLLM with two scripted responses (one for each run)
    llm = TestLLM.from_messages(
        [
            Message(role="assistant", content=[TextContent(text="Task completed")]),
            Message(role="assistant", content=[TextContent(text="Task completed")]),
        ]
    )
    agent = Agent(llm=llm, tools=[])
    conversation = Conversation(agent=agent)

    # First conversation - should end in FINISHED
    conversation.send_message(
        Message(role="user", content=[TextContent(text="First task")])
    )
    conversation.run()
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED

    # Send new message - should transition to IDLE
    conversation.send_message(
        Message(role="user", content=[TextContent(text="Second task")])
    )
    assert conversation.state.execution_status == ConversationExecutionStatus.IDLE

    # Run again - should transition to RUNNING then FINISHED
    conversation.run()
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED


def test_run_exits_immediately_when_already_finished():
    """Test that run() exits immediately when status is already FINISHED."""
    # Use TestLLM with a single scripted response
    llm = TestLLM.from_messages(
        [
            Message(role="assistant", content=[TextContent(text="Task completed")]),
        ]
    )
    agent = Agent(llm=llm, tools=[])
    conversation = Conversation(agent=agent)

    # Complete a task
    conversation.send_message(Message(role="user", content=[TextContent(text="Task")]))
    conversation.run()
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED

    # Call run again without sending a new message
    # Should exit immediately without calling LLM again
    initial_call_count = llm.call_count
    conversation.run()

    # Status should still be FINISHED
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED
    # LLM should not be called again
    assert llm.call_count == initial_call_count


def test_run_exits_immediately_when_stuck():
    """Test that run() exits immediately when status is STUCK."""
    # Use TestLLM with no scripted responses (should not be called)
    llm = TestLLM.from_messages([])
    agent = Agent(llm=llm, tools=[])
    conversation = Conversation(agent=agent)

    # Manually set status to STUCK (simulating stuck detection)
    conversation._state.execution_status = ConversationExecutionStatus.STUCK

    # Call run - should exit immediately
    conversation.run()

    # Status should still be STUCK
    assert conversation.state.execution_status == ConversationExecutionStatus.STUCK
    # LLM should not be called
    assert llm.call_count == 0


def test_execution_status_error_on_max_iterations():
    """Test that status is set to ERROR with clear message when max iterations hit."""
    from openhands.sdk.event.conversation_error import ConversationErrorEvent

    status_during_execution: list[ConversationExecutionStatus] = []
    events_received: list = []

    def _make_tool(conv_state=None, **params) -> Sequence[ToolDefinition]:
        return StatusTransitionTestTool.create(
            executor=StatusCheckingExecutor(status_during_execution)
        )

    register_tool("test_tool", _make_tool)

    # Create a tool call message that will be returned repeatedly
    tool_call_message = Message(
        role="assistant",
        content=[TextContent(text="")],
        tool_calls=[
            MessageToolCall(
                id="call_1",
                name="test_tool",
                arguments='{"command": "test_command"}',
                origin="completion",
            )
        ],
    )

    # Use TestLLM with enough responses to hit max iterations
    # max_iteration_per_run=2 means we need at least 2 tool call responses
    llm = TestLLM.from_messages(
        [
            tool_call_message,
            tool_call_message,
            tool_call_message,  # Extra in case needed
        ]
    )
    agent = Agent(llm=llm, tools=[Tool(name="test_tool")])
    # Set max_iteration_per_run to 2 to quickly hit the limit
    conversation = Conversation(
        agent=agent,
        max_iteration_per_run=2,
        callbacks=[lambda e: events_received.append(e)],
    )

    # Send message and run
    conversation.send_message(
        Message(role="user", content=[TextContent(text="Execute command")])
    )
    conversation.run()

    # Status should be ERROR
    assert conversation.state.execution_status == ConversationExecutionStatus.ERROR

    # Should have emitted a ConversationErrorEvent with clear message
    error_events = [e for e in events_received if isinstance(e, ConversationErrorEvent)]
    assert len(error_events) == 1
    assert error_events[0].code == "MaxIterationsReached"
    assert "maximum iterations limit" in error_events[0].detail
    assert "(2)" in error_events[0].detail  # max_iteration_per_run value
