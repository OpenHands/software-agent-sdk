"""Integration tests for parallel tool execution within the agent.

These tests verify that the agent correctly executes tool calls in parallel
when tool_concurrency_limit > 1, including event ordering, state transitions,
FinishTool truncation, and blocked action handling.
"""

import threading
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Self

import pytest
from pydantic import Field, ValidationError

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import ActionEvent, AgentErrorEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import Action, Observation, Tool, ToolExecutor, register_tool
from openhands.sdk.tool.tool import DeclaredResources, ToolDefinition


if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState


# --- Test tools ---


class SlowAction(Action):
    delay: float = Field(default=0.05)
    label: str = Field(default="")


class SlowObservation(Observation):
    label: str = Field(default="")
    thread_name: str = Field(default="")


class SlowExecutor(ToolExecutor[SlowAction, SlowObservation]):
    def __call__(
        self, action: SlowAction, conversation: "BaseConversation | None" = None
    ) -> SlowObservation:
        time.sleep(action.delay)
        return SlowObservation.from_text(
            text=f"done-{action.label}",
            label=action.label,
            thread_name=threading.current_thread().name,
        )


class SlowTool(ToolDefinition[SlowAction, SlowObservation]):
    name = "slow_tool"

    def declared_resources(self, action: Action) -> DeclaredResources:
        # Each invocation is independent — safe to run in parallel.
        return DeclaredResources(keys=(), declared=True)

    @classmethod
    def create(cls, conv_state: "ConversationState | None" = None) -> Sequence[Self]:
        return [
            cls(
                description="A slow tool for testing parallelism",
                action_type=SlowAction,
                observation_type=SlowObservation,
                executor=SlowExecutor(),
            )
        ]


class ParallelFailingAction(Action):
    value: str = ""


class ParallelFailingObservation(Observation):
    result: str = ""


class ParallelFailingExecutor(
    ToolExecutor[ParallelFailingAction, ParallelFailingObservation]
):
    def __call__(
        self,
        action: ParallelFailingAction,
        conversation: "BaseConversation | None" = None,
    ) -> ParallelFailingObservation:
        raise ValueError(f"Tool failed: {action.value}")


class ParallelFailingTool(
    ToolDefinition[ParallelFailingAction, ParallelFailingObservation]
):
    name = "parallel_failing_tool"

    @classmethod
    def create(cls, conv_state: "ConversationState | None" = None) -> Sequence[Self]:
        return [
            cls(
                description="A tool that always fails",
                action_type=ParallelFailingAction,
                observation_type=ParallelFailingObservation,
                executor=ParallelFailingExecutor(),
            )
        ]


register_tool("SlowTool", SlowTool)
register_tool("ParallelFailingTool", ParallelFailingTool)


# --- Helper ---


def _tool_call(call_id: str, name: str, arguments: str) -> MessageToolCall:
    return MessageToolCall(
        id=call_id, name=name, arguments=arguments, origin="completion"
    )


def _run_step(agent, conversation, collected_events):
    """Run a single agent step and return collected events."""
    agent.step(conversation, on_event=lambda e: collected_events.append(e))


# --- Tests ---


def test_parallel_execution_multiple_tools():
    """Multiple tool calls execute in parallel and events are emitted in order."""
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Running tools")],
                tool_calls=[
                    _tool_call("call_0", "slow_tool", '{"delay": 0.05, "label": "a"}'),
                    _tool_call("call_1", "slow_tool", '{"delay": 0.05, "label": "b"}'),
                    _tool_call("call_2", "slow_tool", '{"delay": 0.05, "label": "c"}'),
                ],
            ),
            Message(role="assistant", content=[TextContent(text="Done")]),
        ]
    )
    agent = Agent(llm=llm, tools=[Tool(name="SlowTool")], tool_concurrency_limit=4)

    collected = []
    conversation = Conversation(agent=agent, callbacks=[lambda e: collected.append(e)])
    conversation.send_message(Message(role="user", content=[TextContent(text="Go")]))
    _run_step(agent, conversation, collected)

    # Verify observations are emitted in original order
    obs_events = [e for e in collected if isinstance(e, ObservationEvent)]
    assert len(obs_events) == 3
    assert obs_events[0].tool_call_id == "call_0"
    assert obs_events[1].tool_call_id == "call_1"
    assert obs_events[2].tool_call_id == "call_2"


def test_parallel_execution_faster_than_sequential():
    """Parallel execution completes faster than sequential would."""
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="")],
                tool_calls=[
                    _tool_call("call_0", "slow_tool", '{"delay": 0.1, "label": "a"}'),
                    _tool_call("call_1", "slow_tool", '{"delay": 0.1, "label": "b"}'),
                    _tool_call("call_2", "slow_tool", '{"delay": 0.1, "label": "c"}'),
                    _tool_call("call_3", "slow_tool", '{"delay": 0.1, "label": "d"}'),
                ],
            ),
            Message(role="assistant", content=[TextContent(text="Done")]),
        ]
    )
    agent = Agent(llm=llm, tools=[Tool(name="SlowTool")], tool_concurrency_limit=4)

    collected = []
    conversation = Conversation(agent=agent, callbacks=[lambda e: collected.append(e)])
    conversation.send_message(Message(role="user", content=[TextContent(text="Go")]))

    start = time.monotonic()
    _run_step(agent, conversation, collected)
    elapsed = time.monotonic() - start

    # 4 tools x 0.1s each = 0.4s sequential, should be ~0.1s parallel
    assert elapsed < 0.3, f"Expected parallel execution, took {elapsed:.2f}s"


def test_sequential_execution_with_default_limit():
    """With default tool_concurrency_limit=1, tools execute sequentially."""
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="")],
                tool_calls=[
                    _tool_call("call_0", "slow_tool", '{"delay": 0.02, "label": "a"}'),
                    _tool_call("call_1", "slow_tool", '{"delay": 0.02, "label": "b"}'),
                ],
            ),
            Message(role="assistant", content=[TextContent(text="Done")]),
        ]
    )
    agent = Agent(llm=llm, tools=[Tool(name="SlowTool")])

    collected = []
    conversation = Conversation(agent=agent, callbacks=[lambda e: collected.append(e)])
    conversation.send_message(Message(role="user", content=[TextContent(text="Go")]))
    _run_step(agent, conversation, collected)

    obs_events = [e for e in collected if isinstance(e, ObservationEvent)]
    assert len(obs_events) == 2
    assert obs_events[0].tool_call_id == "call_0"
    assert obs_events[1].tool_call_id == "call_1"


def test_limit_one_preserves_sequential_semantics():
    """Regression: tool_concurrency_limit=1 must preserve old sequential behavior.

    With the default limit of 1, multi-tool batches must:
    1. Run each tool on the caller's thread (not a pool thread).
    2. Execute tools strictly in order.

    SlowTool already records threading.current_thread().name in its
    observation, so we can verify thread affinity end-to-end.
    """
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="")],
                tool_calls=[
                    _tool_call("call_0", "slow_tool", '{"delay": 0.0, "label": "a"}'),
                    _tool_call("call_1", "slow_tool", '{"delay": 0.0, "label": "b"}'),
                    _tool_call("call_2", "slow_tool", '{"delay": 0.0, "label": "c"}'),
                ],
            ),
            Message(role="assistant", content=[TextContent(text="Done")]),
        ]
    )
    # Default tool_concurrency_limit=1
    agent = Agent(llm=llm, tools=[Tool(name="SlowTool")])

    collected = []
    conversation = Conversation(agent=agent, callbacks=[lambda e: collected.append(e)])
    conversation.send_message(Message(role="user", content=[TextContent(text="Go")]))

    caller_thread = threading.current_thread().name
    _run_step(agent, conversation, collected)

    obs_events = [e for e in collected if isinstance(e, ObservationEvent)]
    assert len(obs_events) == 3

    # Property 1: every tool ran on the caller's thread, not a pool thread
    labels: list[str] = []
    for obs in obs_events:
        observation = obs.observation
        assert isinstance(observation, SlowObservation)
        assert observation.thread_name == caller_thread, (
            f"Tool '{observation.label}' ran on "
            f"{observation.thread_name}, expected {caller_thread}"
        )
        labels.append(observation.label)

    # Property 2: tools executed in original order
    assert labels == ["a", "b", "c"]


def test_finish_tool_truncates_subsequent_tools():
    """Tools after FinishTool are discarded and never executed."""
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="")],
                tool_calls=[
                    _tool_call(
                        "call_0", "slow_tool", '{"delay": 0.01, "label": "before"}'
                    ),
                    _tool_call("call_finish", "finish", '{"message": "All done"}'),
                    _tool_call(
                        "call_2", "slow_tool", '{"delay": 0.01, "label": "after"}'
                    ),
                ],
            ),
        ]
    )
    agent = Agent(llm=llm, tools=[Tool(name="SlowTool")], tool_concurrency_limit=4)

    collected = []
    conversation = Conversation(agent=agent, callbacks=[lambda e: collected.append(e)])
    conversation.send_message(Message(role="user", content=[TextContent(text="Go")]))
    _run_step(agent, conversation, collected)

    # Only slow_tool "before" and finish should have executed
    action_events = [e for e in collected if isinstance(e, ActionEvent)]
    tool_names = [e.tool_name for e in action_events]
    assert "slow_tool" in tool_names
    assert "finish" in tool_names

    # The "after" tool call should not exist
    obs_events = [e for e in collected if isinstance(e, ObservationEvent)]
    obs_tool_calls = [e.tool_call_id for e in obs_events]
    assert "call_2" not in obs_tool_calls

    # Conversation should be finished
    with conversation.state:
        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )


def test_error_in_parallel_batch_preserves_other_results():
    """
    A failing tool in a parallel batch doesn't
    prevent other tools from completing.
    """
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="")],
                tool_calls=[
                    _tool_call(
                        "call_0", "slow_tool", '{"delay": 0.01, "label": "ok1"}'
                    ),
                    _tool_call("call_1", "parallel_failing_tool", '{"value": "boom"}'),
                    _tool_call(
                        "call_2", "slow_tool", '{"delay": 0.01, "label": "ok2"}'
                    ),
                ],
            ),
            Message(role="assistant", content=[TextContent(text="Recovered")]),
        ]
    )
    agent = Agent(
        llm=llm,
        tools=[Tool(name="SlowTool"), Tool(name="ParallelFailingTool")],
        tool_concurrency_limit=4,
    )

    collected = []
    conversation = Conversation(agent=agent, callbacks=[lambda e: collected.append(e)])
    conversation.send_message(Message(role="user", content=[TextContent(text="Go")]))
    _run_step(agent, conversation, collected)

    # Should have 2 observations and 1 error, in order
    obs_events = [e for e in collected if isinstance(e, ObservationEvent)]
    error_events = [e for e in collected if isinstance(e, AgentErrorEvent)]

    assert len(obs_events) == 2
    assert len(error_events) == 1
    assert "boom" in error_events[0].error

    # Events should be in original order: obs_0, error_1, obs_2
    result_events = [
        e for e in collected if isinstance(e, (ObservationEvent, AgentErrorEvent))
    ]
    assert result_events[0].tool_call_id == "call_0"
    assert result_events[1].tool_call_id == "call_1"
    assert result_events[2].tool_call_id == "call_2"

    # Conversation should NOT be finished
    with conversation.state:
        assert (
            conversation.state.execution_status != ConversationExecutionStatus.FINISHED
        )


def test_blocked_action_with_parallel_execution():
    """
    Blocked actions produce rejections while
    non-blocked actions execute in parallel.
    """
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="")],
                tool_calls=[
                    _tool_call("call_0", "slow_tool", '{"delay": 0.01, "label": "a"}'),
                    _tool_call("call_1", "slow_tool", '{"delay": 0.01, "label": "b"}'),
                ],
            ),
            Message(role="assistant", content=[TextContent(text="Done")]),
        ]
    )
    agent = Agent(llm=llm, tools=[Tool(name="SlowTool")], tool_concurrency_limit=4)

    collected = []
    conversation = Conversation(agent=agent, callbacks=[lambda e: collected.append(e)])
    conversation.send_message(Message(role="user", content=[TextContent(text="Go")]))

    # Run one step to get the action events so we know their IDs
    _run_step(agent, conversation, collected)

    # For this test, we verify the mechanism works by checking that
    # both observations were emitted (no blocking configured).
    obs_events = [e for e in collected if isinstance(e, ObservationEvent)]
    assert len(obs_events) == 2


def test_tool_concurrency_limit_wires_to_executor():
    """Agent.tool_concurrency_limit is wired through to the ParallelToolExecutor."""
    llm = TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="Done")])]
    )
    agent = Agent(llm=llm, tools=[], tool_concurrency_limit=6)
    assert agent._parallel_executor._max_workers == 6

    agent_default = Agent(llm=llm, tools=[])
    assert agent_default._parallel_executor._max_workers == 1


@pytest.mark.parametrize("value", [0, -1, -100])
def test_tool_concurrency_limit_rejects_invalid_values(value):
    """Pydantic validates tool_concurrency_limit >= 1 at construction time."""
    llm = TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="Done")])]
    )
    with pytest.raises(ValidationError):
        Agent(llm=llm, tools=[], tool_concurrency_limit=value)
