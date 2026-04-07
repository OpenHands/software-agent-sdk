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
- STUCK -> IDLE (when new message sent) -> RUNNING (when run() is called)
- STUCK -> RUNNING (when run() is called directly)
- FINISHED -> remain unchanged (run() exits immediately without new message)
"""

import copy
import threading
from collections.abc import Sequence
from typing import Any, ClassVar

from pydantic import PrivateAttr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import ConversationIterationLimitEvent, MessageEvent
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


class CapturingTestLLM(TestLLM):
    """TestLLM variant that records the message history sent to the LLM."""

    _captured_messages: list[list[Message]] = PrivateAttr(default_factory=list)

    def __init__(
        self, *, scripted_responses: list[Message | Exception], **kwargs: Any
    ) -> None:
        kwargs.setdefault("model", "test-model")
        super().__init__(scripted_responses=scripted_responses, **kwargs)

    @property
    def captured_messages(self) -> list[list[Message]]:
        return self._captured_messages

    def completion(self, messages: list[Message], *args, **kwargs):
        self._captured_messages.append(copy.deepcopy(messages))
        return super().completion(messages, *args, **kwargs)


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


def test_run_recovers_from_stuck():
    """Test that run() resets STUCK status and lets the agent continue.

    When a conversation is STUCK (e.g. stuck detector triggered or
    persisted STUCK state from a previous session), calling run() should
    reset the status to RUNNING so the agent can retry.  Without this
    reset, a persisted STUCK state would permanently kill the session.
    """
    # Provide a finish response so the agent can complete after unsticking.
    llm = TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="Recovered")])]
    )
    agent = Agent(llm=llm, tools=[])
    conversation = Conversation(agent=agent)

    # Seed a user message so the agent has context to work with
    conversation.send_message(
        Message(role="user", content=[TextContent(text="Please continue")])
    )

    # Simulate stuck detection persisted from previous session
    conversation._state.execution_status = ConversationExecutionStatus.STUCK

    conversation.run()

    # Agent should have recovered and finished normally
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED
    assert llm.call_count == 1


def test_send_message_resets_stuck_to_idle():
    """Test STUCK → IDLE transition when a new user message arrives.

    A new user message is an implicit signal to unstick the conversation,
    analogous to how FINISHED → IDLE works.
    """
    llm = TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="Done")])]
    )
    agent = Agent(llm=llm, tools=[])
    conversation = Conversation(agent=agent)

    # Simulate stuck state
    conversation._state.execution_status = ConversationExecutionStatus.STUCK

    # Sending a new message should reset STUCK → IDLE
    conversation.send_message(
        Message(role="user", content=[TextContent(text="Try again")])
    )
    assert conversation.state.execution_status == ConversationExecutionStatus.IDLE

    # Running should proceed normally: IDLE → RUNNING → FINISHED
    conversation.run()
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED


def test_execution_status_iteration_limit_on_max_iterations():
    """Test that max iterations ends with a dedicated iteration-limit status."""

    status_during_execution: list[ConversationExecutionStatus] = []
    events_received: list = []

    def _make_tool(conv_state=None, **params) -> Sequence[ToolDefinition]:
        return StatusTransitionTestTool.create(
            executor=StatusCheckingExecutor(status_during_execution)
        )

    register_tool("test_tool", _make_tool)

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

    llm = TestLLM.from_messages(
        [
            tool_call_message,
            tool_call_message,
            tool_call_message,
        ]
    )
    agent = Agent(llm=llm, tools=[Tool(name="test_tool")])
    conversation = Conversation(
        agent=agent,
        max_iteration_per_run=2,
        callbacks=[lambda e: events_received.append(e)],
    )

    conversation.send_message(
        Message(role="user", content=[TextContent(text="Execute command")])
    )
    conversation.run()

    assert (
        conversation.state.execution_status
        == ConversationExecutionStatus.ITERATION_LIMIT
    )

    limit_events = [
        e for e in events_received if isinstance(e, ConversationIterationLimitEvent)
    ]
    assert len(limit_events) == 1
    assert limit_events[0].iteration == 2
    assert limit_events[0].max_iterations == 2
    assert "maximum iterations limit" in limit_events[0].detail
    assert "(2)" in limit_events[0].detail


def test_final_step_warning_is_visible_to_llm():
    """The LLM should receive an explicit final-step wrap-up warning."""

    def _make_tool(conv_state=None, **params) -> Sequence[ToolDefinition]:
        return StatusTransitionTestTool.create(executor=StatusCheckingExecutor([]))

    register_tool("test_tool", _make_tool)

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
    finish_message = Message(role="assistant", content=[TextContent(text="Done")])

    llm = CapturingTestLLM(
        scripted_responses=[tool_call_message, finish_message],
        usage_id="capturing-test-llm",
    )
    agent = Agent(llm=llm, tools=[Tool(name="test_tool")])
    conversation = Conversation(agent=agent, max_iteration_per_run=2)

    conversation.send_message(
        Message(role="user", content=[TextContent(text="Execute command")])
    )
    conversation.run()

    assert len(llm.captured_messages) == 2
    second_call_messages = llm.captured_messages[1]
    final_step_messages = [
        content.text
        for message in second_call_messages
        if message.role == "user"
        for content in message.content
        if isinstance(content, TextContent) and "FINAL step" in content.text
    ]
    assert len(final_step_messages) == 1
    assert "Provide your best possible answer now" in final_step_messages[0]


def test_execution_status_finished_on_final_iteration():
    """FINISHED is preserved when agent completes on its final iteration.

    Regression test for: agent's FINISHED status being overwritten with
    ERROR when the task completes exactly on the max_iteration_per_run
    boundary.
    """

    events_received: list = []

    def _make_tool(conv_state=None, **params) -> Sequence[ToolDefinition]:
        return StatusTransitionTestTool.create(executor=StatusCheckingExecutor([]))

    register_tool("test_tool", _make_tool)

    # Two tool-call iterations followed by a text response on the 3rd (final) iteration.
    # A text-only assistant message causes the agent to set status to FINISHED.
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
    finish_message = Message(
        role="assistant", content=[TextContent(text="Task completed successfully")]
    )

    llm = TestLLM.from_messages(
        [
            tool_call_message,  # iteration 1
            tool_call_message,  # iteration 2
            finish_message,  # iteration 3 (final) — agent finishes here
        ]
    )
    agent = Agent(llm=llm, tools=[Tool(name="test_tool")])
    conversation = Conversation(
        agent=agent,
        max_iteration_per_run=3,
        callbacks=[lambda e: events_received.append(e)],
    )

    conversation.send_message(
        Message(role="user", content=[TextContent(text="Execute command")])
    )
    conversation.run()

    # Status must be FINISHED, not ERROR
    assert (
        conversation.state.execution_status == ConversationExecutionStatus.FINISHED
    ), (
        f"Expected FINISHED but got {conversation.state.execution_status}. "
        "Agent completing on the final iteration should not be treated as an error."
    )

    # No iteration-limit event should have been emitted when agent finishes in time
    limit_events = [
        e for e in events_received if isinstance(e, ConversationIterationLimitEvent)
    ]
    assert len(limit_events) == 0, (
        "Expected no iteration-limit event when agent finishes on final iteration"
    )
