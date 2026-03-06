"""Tests for conversation.rerun_actions() functionality."""

import pytest
from pydantic import SecretStr

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.conversation.types import (
    ConversationCallbackType,
    ConversationTokenCallbackType,
)
from openhands.sdk.event import ActionEvent
from openhands.sdk.event.llm_convertible import MessageEvent, SystemPromptEvent
from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent
from openhands.sdk.tool import (
    Action,
    Observation,
    Tool,
    ToolDefinition,
    ToolExecutor,
    register_tool as register_tool_public,
    registry as tool_registry,
)


def _make_action_event(
    tool_name: str,
    action: Action,
    tool_call_id: str = "tc1",
) -> ActionEvent:
    """Helper to create ActionEvent with all required fields."""
    return ActionEvent(
        source="agent",
        thought=[TextContent(text="test thought")],
        action=action,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_call=MessageToolCall(
            id=tool_call_id,
            name=tool_name,
            arguments="{}",
            origin="completion",
        ),
        llm_response_id="response_1",
    )


# Track execution counts for testing
execution_counts: dict[str, int] = {}


class RerunTestAction(Action):
    """Test action for rerun tests."""

    value: str = "test"


class RerunTestObservation(Observation):
    """Test observation for rerun tests."""

    result: str = ""
    execution_count: int = 0


class RerunTestExecutor(ToolExecutor[RerunTestAction, RerunTestObservation]):
    """Test executor that tracks execution counts."""

    def __call__(
        self,
        action: RerunTestAction,
        conversation: "LocalConversation | None" = None,
    ) -> RerunTestObservation:
        # Track how many times each action value was executed
        key = action.value
        execution_counts[key] = execution_counts.get(key, 0) + 1
        return RerunTestObservation.from_text(
            f"executed: {action.value} (count: {execution_counts[key]})",
            result=f"result_{action.value}",
            execution_count=execution_counts[key],
        )


class RerunTestTool(ToolDefinition[RerunTestAction, RerunTestObservation]):
    """Test tool for rerun tests."""

    @classmethod
    def create(cls, conv_state=None, **params):
        return [
            cls(
                description="A test tool for testing rerun_actions",
                action_type=RerunTestAction,
                observation_type=RerunTestObservation,
                executor=RerunTestExecutor(),
            )
        ]


@pytest.fixture(autouse=True)
def _reset_execution_counts():
    """Reset execution counts before each test."""
    execution_counts.clear()
    yield
    execution_counts.clear()


@pytest.fixture(autouse=True)
def _tool_registry_snapshot():
    registry_snapshot = dict(tool_registry._REG)
    module_snapshot = dict(tool_registry._MODULE_QUALNAMES)
    register_tool_public(RerunTestTool.name, RerunTestTool)
    try:
        yield
    finally:
        tool_registry._REG.clear()
        tool_registry._REG.update(registry_snapshot)
        tool_registry._MODULE_QUALNAMES.clear()
        tool_registry._MODULE_QUALNAMES.update(module_snapshot)


class RerunDummyAgent(AgentBase):
    """Dummy agent for testing rerun_actions."""

    def __init__(self, tools=None):
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        super().__init__(llm=llm, tools=tools or [])

    def init_state(
        self, state: ConversationState, on_event: ConversationCallbackType
    ) -> None:
        super().init_state(state, on_event)
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


def test_rerun_actions_empty_conversation():
    """Test rerun_actions on a conversation with no actions."""
    agent = RerunDummyAgent(tools=[Tool(name="rerun_test", params={})])
    conversation = Conversation(agent=agent)

    # Rerun on empty conversation should return empty list
    observations = conversation.rerun_actions()
    assert observations == []


def test_rerun_actions_basic():
    """Test basic rerun_actions functionality."""
    agent = RerunDummyAgent(tools=[Tool(name="rerun_test", params={})])
    conversation = Conversation(agent=agent)

    # Execute some tools to create action events
    action1 = RerunTestAction(value="first")
    action2 = RerunTestAction(value="second")

    # Manually add action events to simulate a conversation history
    conversation._ensure_agent_ready()
    action_event = _make_action_event("rerun_test", action1, "tc1")
    conversation._state.events.append(action_event)

    action_event2 = _make_action_event("rerun_test", action2, "tc2")
    conversation._state.events.append(action_event2)

    # Now rerun all actions
    observations = conversation.rerun_actions()

    # Should have executed both actions
    assert len(observations) == 2
    # Cast to our specific observation type to access result
    obs1 = observations[0]
    obs2 = observations[1]
    assert isinstance(obs1, RerunTestObservation)
    assert isinstance(obs2, RerunTestObservation)
    assert obs1.result == "result_first"
    assert obs2.result == "result_second"
    assert execution_counts["first"] == 1
    assert execution_counts["second"] == 1


def test_rerun_actions_preserves_original_observations():
    """Test that rerun_actions doesn't modify the original event log."""
    agent = RerunDummyAgent(tools=[Tool(name="rerun_test", params={})])
    conversation = Conversation(agent=agent)

    # Add an action event
    conversation._ensure_agent_ready()
    action = RerunTestAction(value="preserve_test")
    action_event = _make_action_event("rerun_test", action, "tc1")
    conversation._state.events.append(action_event)

    # Count events before rerun
    events_before = len(list(conversation._state.events))

    # Rerun actions
    observations = conversation.rerun_actions()

    # Count events after rerun - should be the same
    events_after = len(list(conversation._state.events))

    assert events_before == events_after
    assert len(observations) == 1


def test_rerun_actions_skips_none_actions():
    """Test that rerun_actions skips ActionEvents with action=None."""
    agent = RerunDummyAgent(tools=[Tool(name="rerun_test", params={})])
    conversation = Conversation(agent=agent)

    conversation._ensure_agent_ready()

    # Add an action event with action=None (failed validation)
    action_event_none = ActionEvent(
        source="agent",
        thought=[TextContent(text="test")],
        tool_name="rerun_test",
        tool_call_id="tc1",
        tool_call=MessageToolCall(
            id="tc1", name="rerun_test", arguments="{}", origin="completion"
        ),
        llm_response_id="resp1",
        action=None,  # Failed validation
    )
    conversation._state.events.append(action_event_none)

    # Add a valid action event
    action = RerunTestAction(value="valid")
    action_event_valid = _make_action_event("rerun_test", action, "tc2")
    conversation._state.events.append(action_event_valid)

    # Rerun should only execute the valid action
    observations = conversation.rerun_actions()

    assert len(observations) == 1
    obs = observations[0]
    assert isinstance(obs, RerunTestObservation)
    assert obs.result == "result_valid"


def test_rerun_actions_missing_tool_raises():
    """Test that rerun_actions raises KeyError for missing tools."""
    agent = RerunDummyAgent(tools=[])  # No tools registered
    conversation = Conversation(agent=agent)

    conversation._ensure_agent_ready()

    # Add an action event for a tool that doesn't exist
    action = RerunTestAction(value="test")
    action_event = _make_action_event("rerun_test", action, "tc1")
    conversation._state.events.append(action_event)

    with pytest.raises(KeyError) as exc_info:
        conversation.rerun_actions()

    assert "rerun_test" in str(exc_info.value)
    assert "not found during rerun" in str(exc_info.value)


def test_rerun_can_be_called_manually():
    """Test that rerun_actions can be called manually after initialization."""
    agent = RerunDummyAgent(tools=[Tool(name="rerun_test", params={})])
    conversation = Conversation(agent=agent)

    conversation._ensure_agent_ready()
    action = RerunTestAction(value="manual")
    action_event = _make_action_event("rerun_test", action, "tc1")
    conversation._state.events.append(action_event)

    # Call rerun manually (not during init)
    observations = conversation.rerun_actions()

    assert len(observations) == 1
    assert execution_counts["manual"] == 1

    # Can call again
    observations2 = conversation.rerun_actions()

    assert len(observations2) == 1
    assert execution_counts["manual"] == 2  # Executed twice now
