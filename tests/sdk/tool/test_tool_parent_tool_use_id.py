"""Unit tests for Task 5: threading parent_tool_use_id through ToolDefinition.__call__
and _execute_action_event.

Test 1: ToolDefinition.__call__ forwards parent_tool_use_id to executors that accept it;
        executors without the param still run fine.
Test 2: _execute_action_event passes action_event.tool_call.id as parent_tool_use_id.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Self
from unittest.mock import MagicMock, patch

import pytest
from pydantic import Field

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import Action, Observation, Tool, ToolExecutor, register_tool
from openhands.sdk.tool.tool import ToolDefinition

if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


# ---------------------------------------------------------------------------
# Shared action / observation types
# ---------------------------------------------------------------------------


class SimpleAction(Action):
    value: str = Field(default="")


class SimpleObservation(Observation):
    result: str = Field(default="")


# ---------------------------------------------------------------------------
# Test 1: ToolDefinition.__call__ forwards / ignores parent_tool_use_id correctly
# ---------------------------------------------------------------------------


class AwareExecutor(ToolExecutor):
    """Executor that accepts parent_tool_use_id and records it."""

    received_id: list[str | None]

    def __init__(self):
        self.received_id = []

    def __call__(
        self,
        action: SimpleAction,
        conversation=None,
        *,
        parent_tool_use_id: str | None = None,
    ) -> SimpleObservation:
        self.received_id.append(parent_tool_use_id)
        return SimpleObservation(result="aware")


class UnawareExecutor(ToolExecutor):
    """Executor WITHOUT parent_tool_use_id param — must not receive it."""

    called: list[bool]

    def __init__(self):
        self.called = []

    def __call__(
        self, action: SimpleAction, conversation=None
    ) -> SimpleObservation:
        self.called.append(True)
        return SimpleObservation(result="unaware")


class SimpleTool(ToolDefinition[SimpleAction, SimpleObservation]):
    name = "simple_test_tool_ptuid"

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["SimpleTool"]:
        return [cls(**params)]


def test_tool_definition_call_forwards_id_to_aware_executor():
    """parent_tool_use_id is forwarded when executor.__call__ accepts it."""
    executor = AwareExecutor()
    tool = SimpleTool(
        description="Test tool",
        action_type=SimpleAction,
        observation_type=SimpleObservation,
        executor=executor,
    )

    action = SimpleAction(value="x")
    result = tool(action, parent_tool_use_id="toolu_abc123")

    assert isinstance(result, SimpleObservation)
    assert executor.received_id == ["toolu_abc123"]


def test_tool_definition_call_without_id_passes_none_to_aware_executor():
    """When parent_tool_use_id is omitted it defaults to None."""
    executor = AwareExecutor()
    tool = SimpleTool(
        description="Test tool",
        action_type=SimpleAction,
        observation_type=SimpleObservation,
        executor=executor,
    )

    action = SimpleAction(value="x")
    tool(action)

    assert executor.received_id == [None]


def test_tool_definition_call_does_not_break_unaware_executor():
    """Passing parent_tool_use_id must not cause TypeError for executors without it."""
    executor = UnawareExecutor()
    tool = SimpleTool(
        description="Test tool",
        action_type=SimpleAction,
        observation_type=SimpleObservation,
        executor=executor,
    )

    action = SimpleAction(value="x")
    # This must NOT raise TypeError even though parent_tool_use_id is supplied
    result = tool(action, parent_tool_use_id="toolu_xyz")

    assert isinstance(result, SimpleObservation)
    assert executor.called == [True]


# ---------------------------------------------------------------------------
# Test 2: _execute_action_event passes action_event.tool_call.id
# ---------------------------------------------------------------------------


class CapturingExecutor(ToolExecutor):
    """Executor that captures every parent_tool_use_id it receives."""

    ids: list[str | None]

    def __init__(self):
        self.ids = []

    def __call__(
        self,
        action: SimpleAction,
        conversation=None,
        *,
        parent_tool_use_id: str | None = None,
    ) -> SimpleObservation:
        self.ids.append(parent_tool_use_id)
        return SimpleObservation(result="captured")


# Module-level executor so tests can inspect it after agent.step()
_capturing_executor = CapturingExecutor()


class CapturingTool(ToolDefinition[SimpleAction, SimpleObservation]):
    name = "capturing_tool_ptuid"

    @classmethod
    def create(cls, conv_state: "ConversationState | None" = None) -> Sequence[Self]:
        return [
            cls(
                description="Captures parent_tool_use_id",
                action_type=SimpleAction,
                observation_type=SimpleObservation,
                executor=_capturing_executor,
            )
        ]


register_tool("CapturingTool", CapturingTool)


def test_execute_action_event_passes_tool_call_id():
    """_execute_action_event must pass action_event.tool_call.id as parent_tool_use_id."""
    expected_id = "toolu_test_call_99"
    _capturing_executor.ids.clear()

    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="")],
                tool_calls=[
                    MessageToolCall(
                        id=expected_id,
                        name="capturing_tool_ptuid",
                        arguments='{"value": "hello"}',
                        origin="completion",
                    )
                ],
            ),
            # Second response: finish
            Message(role="assistant", content=[TextContent(text="Done")]),
        ]
    )

    agent = Agent(llm=llm, tools=[Tool(name="CapturingTool")])

    collected = []
    conversation = Conversation(agent=agent, callbacks=[lambda e: collected.append(e)])
    conversation.send_message(Message(role="user", content=[TextContent(text="Go")]))

    # Run one step so the tool call is dispatched
    agent.step(conversation, on_event=lambda e: collected.append(e))

    assert len(_capturing_executor.ids) == 1, (
        f"Expected 1 call, got {len(_capturing_executor.ids)}"
    )
    assert _capturing_executor.ids[0] == expected_id, (
        f"Expected parent_tool_use_id={expected_id!r}, got {_capturing_executor.ids[0]!r}"
    )
