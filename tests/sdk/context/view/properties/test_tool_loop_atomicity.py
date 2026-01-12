"""Tests for ToolLoopAtomicityProperty.

This module tests the ToolLoopAtomicityProperty class independently from the View class.
The property ensures that tool loops (thinking blocks + consecutive tool calls) remain
atomic units that cannot be split.

A tool loop consists of:
- An initial batch containing thinking blocks
- All subsequent consecutive ActionEvent/ObservationEvent batches
- Terminated by the first non-ActionEvent/ObservationEvent
"""

from collections.abc import Sequence

from openhands.sdk.context.view.properties.tool_loop_atomicity import (
    ToolLoopAtomicityProperty,
)
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.llm import (
    Message,
    MessageToolCall,
    RedactedThinkingBlock,
    TextContent,
    ThinkingBlock,
)
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation


def create_action_event(
    llm_response_id: str,
    tool_call_id: str,
    tool_name: str = "test_tool",
    thinking_blocks: Sequence[ThinkingBlock | RedactedThinkingBlock] | None = None,
) -> ActionEvent:
    """Helper to create an ActionEvent with specified IDs."""
    action = MCPToolAction(data={})

    tool_call = MessageToolCall(
        id=tool_call_id,
        name=tool_name,
        arguments="{}",
        origin="completion",
    )

    return ActionEvent(
        thought=[TextContent(text="Test thought")],
        thinking_blocks=list(thinking_blocks) if thinking_blocks else [],
        action=action,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_call=tool_call,
        llm_response_id=llm_response_id,
        source="agent",
    )


def create_observation_event(
    tool_call_id: str, content: str = "Success", tool_name: str = "test_tool"
) -> ObservationEvent:
    """Helper to create an ObservationEvent."""
    observation = MCPToolObservation.from_text(
        text=content,
        tool_name=tool_name,
    )
    return ObservationEvent(
        observation=observation,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        action_id="action_event_id",
        source="environment",
    )


def message_event(content: str) -> MessageEvent:
    """Helper to create a MessageEvent."""
    return MessageEvent(
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )


# ============================================================================
# Tests for enforce() method
# ============================================================================


def test_enforce_complete_tool_loop_no_removal() -> None:
    """Test that complete tool loops are not removed."""
    thinking = [
        ThinkingBlock(type="thinking", thinking="Thinking...", signature="sig1")
    ]

    action1 = create_action_event("response_1", "call_1", thinking_blocks=thinking)
    obs1 = create_observation_event("call_1")
    action2 = create_action_event("response_2", "call_2")
    obs2 = create_observation_event("call_2")

    all_events = [action1, obs1, action2, obs2]
    current_view = [action1, obs1, action2, obs2]

    prop = ToolLoopAtomicityProperty()
    to_remove = prop.enforce(current_view, all_events)

    assert len(to_remove) == 0


def test_enforce_partial_tool_loop_removed() -> None:
    """Test that partial tool loops are completely removed."""
    thinking = [
        ThinkingBlock(type="thinking", thinking="Thinking...", signature="sig1")
    ]

    action1 = create_action_event("response_1", "call_1", thinking_blocks=thinking)
    obs1 = create_observation_event("call_1")
    action2 = create_action_event("response_2", "call_2")
    obs2 = create_observation_event("call_2")

    # Complete tool loop in all_events
    all_events = [action1, obs1, action2, obs2]

    # But view is missing action2 (partial loop)
    current_view = [action1, obs1, obs2]

    prop = ToolLoopAtomicityProperty()
    to_remove = prop.enforce(current_view, all_events)

    # Should remove all events from the partial tool loop
    assert action1.id in to_remove
    assert obs1.id in to_remove
    assert obs2.id in to_remove


def test_enforce_no_thinking_blocks_no_enforcement() -> None:
    """Test that actions without thinking blocks don't trigger tool loop enforcement."""
    action1 = create_action_event("response_1", "call_1")
    obs1 = create_observation_event("call_1")
    action2 = create_action_event("response_2", "call_2")
    obs2 = create_observation_event("call_2")

    all_events = [action1, obs1, action2, obs2]
    current_view = [action1, obs1]  # Partial view, but no thinking blocks

    prop = ToolLoopAtomicityProperty()
    to_remove = prop.enforce(current_view, all_events)

    # No tool loop, so no enforcement
    assert len(to_remove) == 0


def test_enforce_tool_loop_terminated_by_message() -> None:
    """Test that tool loops are correctly terminated by non-action/observation."""
    thinking = [
        ThinkingBlock(type="thinking", thinking="Thinking...", signature="sig1")
    ]

    action1 = create_action_event("response_1", "call_1", thinking_blocks=thinking)
    obs1 = create_observation_event("call_1")
    msg = message_event("User message")
    action2 = create_action_event("response_2", "call_2")
    obs2 = create_observation_event("call_2")

    all_events = [action1, obs1, msg, action2, obs2]

    # View has first part of loop but not msg
    current_view = [action1, obs1, action2, obs2]

    prop = ToolLoopAtomicityProperty()
    to_remove = prop.enforce(current_view, all_events)

    # Tool loop is just action1+obs1 (terminated by msg)
    # action2/obs2 are separate, so if we're missing msg, the loop is still complete
    # Actually, looking at the all_events, the loop is action1+obs1, terminated by msg
    # current_view has action1+obs1 which is the complete loop, so nothing to remove
    assert len(to_remove) == 0


def test_enforce_multiple_batches_in_tool_loop() -> None:
    """Test tool loop spanning multiple batches."""
    thinking = [
        ThinkingBlock(type="thinking", thinking="Thinking...", signature="sig1")
    ]

    # First batch with thinking
    action1 = create_action_event("response_1", "call_1", thinking_blocks=thinking)
    action2 = create_action_event("response_1", "call_2")
    obs1 = create_observation_event("call_1")
    obs2 = create_observation_event("call_2")

    # Second batch (extends the loop)
    action3 = create_action_event("response_2", "call_3")
    obs3 = create_observation_event("call_3")

    all_events = [action1, action2, obs1, obs2, action3, obs3]

    # View is missing obs3 (partial loop)
    current_view = [action1, action2, obs1, obs2, action3]

    prop = ToolLoopAtomicityProperty()
    to_remove = prop.enforce(current_view, all_events)

    # Should remove all events from the partial tool loop
    assert action1.id in to_remove
    assert action2.id in to_remove
    assert obs1.id in to_remove
    assert obs2.id in to_remove
    assert action3.id in to_remove


def test_enforce_empty_view() -> None:
    """Test enforce with empty view."""
    thinking = [
        ThinkingBlock(type="thinking", thinking="Thinking...", signature="sig1")
    ]

    action = create_action_event("response_1", "call_1", thinking_blocks=thinking)
    obs = create_observation_event("call_1")

    all_events = [action, obs]
    current_view = []

    prop = ToolLoopAtomicityProperty()
    to_remove = prop.enforce(current_view, all_events)

    assert len(to_remove) == 0


def test_enforce_redacted_thinking_blocks() -> None:
    """Test that redacted thinking blocks also trigger tool loop logic."""
    thinking = [RedactedThinkingBlock(type="redacted_thinking", data="redacted")]

    action1 = create_action_event("response_1", "call_1", thinking_blocks=thinking)
    obs1 = create_observation_event("call_1")
    action2 = create_action_event("response_2", "call_2")
    obs2 = create_observation_event("call_2")

    all_events = [action1, obs1, action2, obs2]
    current_view = [action1, obs1]  # Missing the continuation

    prop = ToolLoopAtomicityProperty()
    to_remove = prop.enforce(current_view, all_events)

    # action1 has thinking, action2 is consecutive (all action/obs between)
    # So the full loop is action1, obs1, action2, obs2
    # current_view only has action1, obs1, so it's partial
    assert len(to_remove) == 2


# ============================================================================
# Tests for manipulation_indices() method
# ============================================================================


def test_manipulation_indices_no_thinking_blocks() -> None:
    """Test that without thinking blocks, all indices are valid."""
    action1 = create_action_event("response_1", "call_1")
    obs1 = create_observation_event("call_1")
    action2 = create_action_event("response_2", "call_2")
    obs2 = create_observation_event("call_2")

    events = [action1, obs1, action2, obs2]

    prop = ToolLoopAtomicityProperty()
    indices = prop.manipulation_indices(events, events)

    # No tool loops, so all indices are valid
    assert indices == {0, 1, 2, 3, 4}


def test_manipulation_indices_simple_tool_loop() -> None:
    """Test manipulation indices with a simple tool loop."""
    thinking = [
        ThinkingBlock(type="thinking", thinking="Thinking...", signature="sig1")
    ]

    action = create_action_event("response_1", "call_1", thinking_blocks=thinking)
    obs = create_observation_event("call_1")

    events = [action, obs]

    prop = ToolLoopAtomicityProperty()
    indices = prop.manipulation_indices(events, events)

    # Tool loop spans indices 0-1, can only manipulate at boundaries
    assert indices == {0, 2}


def test_manipulation_indices_tool_loop_with_continuation() -> None:
    """Test manipulation indices when tool loop continues across batches."""
    thinking = [
        ThinkingBlock(type="thinking", thinking="Thinking...", signature="sig1")
    ]

    # Batch 1 with thinking
    action1 = create_action_event("response_1", "call_1", thinking_blocks=thinking)
    obs1 = create_observation_event("call_1")

    # Batch 2 (consecutive, extends the loop)
    action2 = create_action_event("response_2", "call_2")
    obs2 = create_observation_event("call_2")

    events = [action1, obs1, action2, obs2]

    prop = ToolLoopAtomicityProperty()
    indices = prop.manipulation_indices(events, events)

    # Entire sequence is one tool loop
    assert indices == {0, 4}


def test_manipulation_indices_tool_loop_terminated_by_message() -> None:
    """Test that messages terminate tool loops."""
    thinking = [
        ThinkingBlock(type="thinking", thinking="Thinking...", signature="sig1")
    ]

    action1 = create_action_event("response_1", "call_1", thinking_blocks=thinking)
    obs1 = create_observation_event("call_1")
    msg = message_event("User message")
    action2 = create_action_event("response_2", "call_2")
    obs2 = create_observation_event("call_2")

    events = [action1, obs1, msg, action2, obs2]

    prop = ToolLoopAtomicityProperty()
    indices = prop.manipulation_indices(events, events)

    # Tool loop is action1+obs1 (indices 0-1), terminated by msg at 2
    # action2+obs2 form a separate batch (no thinking blocks)
    # Can manipulate at: 0, 2, 3, 4, 5
    # Cannot manipulate at: 1 (within tool loop)
    assert indices == {0, 2, 3, 4, 5}


def test_manipulation_indices_multiple_tool_loops() -> None:
    """Test multiple separate tool loops."""
    thinking1 = [
        ThinkingBlock(type="thinking", thinking="Thinking 1", signature="sig1")
    ]
    thinking2 = [
        ThinkingBlock(type="thinking", thinking="Thinking 2", signature="sig2")
    ]

    # First tool loop
    action1 = create_action_event("response_1", "call_1", thinking_blocks=thinking1)
    obs1 = create_observation_event("call_1")

    msg = message_event("Between loops")

    # Second tool loop
    action2 = create_action_event("response_2", "call_2", thinking_blocks=thinking2)
    obs2 = create_observation_event("call_2")

    events = [action1, obs1, msg, action2, obs2]

    prop = ToolLoopAtomicityProperty()
    indices = prop.manipulation_indices(events, events)

    # Loop1: 0-1, msg: 2, Loop2: 3-4
    # Can manipulate at: 0, 2, 3, 5
    # Cannot manipulate at: 1 (within loop1), 4 (within loop2)
    assert indices == {0, 2, 3, 5}


def test_manipulation_indices_multi_batch_tool_loop() -> None:
    """Test tool loop spanning multiple action batches."""
    thinking = [
        ThinkingBlock(type="thinking", thinking="Thinking...", signature="sig1")
    ]

    # Batch 1 with thinking (2 actions)
    action1_1 = create_action_event("response_1", "call_1", thinking_blocks=thinking)
    action1_2 = create_action_event("response_1", "call_2")
    obs1_1 = create_observation_event("call_1")
    obs1_2 = create_observation_event("call_2")

    # Batch 2 (consecutive, extends loop)
    action2 = create_action_event("response_2", "call_3")
    obs2 = create_observation_event("call_3")

    # Batch 3 (consecutive, extends loop)
    action3 = create_action_event("response_3", "call_4")
    obs3 = create_observation_event("call_4")

    msg = message_event("End")

    events = [
        action1_1,
        action1_2,
        obs1_1,
        obs1_2,
        action2,
        obs2,
        action3,
        obs3,
        msg,
    ]

    prop = ToolLoopAtomicityProperty()
    indices = prop.manipulation_indices(events, events)

    # Entire tool loop spans 0-7, terminated by msg at 8
    # Can manipulate at: 0, 8, 9
    assert indices == {0, 8, 9}


def test_manipulation_indices_empty_events() -> None:
    """Test with empty event list."""
    prop = ToolLoopAtomicityProperty()
    indices = prop.manipulation_indices([], [])

    assert indices == {0}


def test_manipulation_indices_single_message() -> None:
    """Test with single message event."""
    msg = message_event("Test")

    prop = ToolLoopAtomicityProperty()
    indices = prop.manipulation_indices([msg], [msg])

    # No tool loops, all indices valid
    assert indices == {0, 1}


def test_manipulation_indices_interleaved_observations() -> None:
    """Test tool loop with observations interleaved between actions."""
    thinking = [
        ThinkingBlock(type="thinking", thinking="Thinking...", signature="sig1")
    ]

    # Batch with thinking
    action1 = create_action_event("response_1", "call_1", thinking_blocks=thinking)
    action2 = create_action_event("response_1", "call_2")

    obs1 = create_observation_event("call_1")

    # Another batch (consecutive)
    action3 = create_action_event("response_2", "call_3")

    obs2 = create_observation_event("call_2")
    obs3 = create_observation_event("call_3")

    events = [action1, action2, obs1, action3, obs2, obs3]

    prop = ToolLoopAtomicityProperty()
    indices = prop.manipulation_indices(events, events)

    # All are actions/observations following thinking batch, so one big loop
    assert indices == {0, 6}


def test_manipulation_indices_complex_scenario() -> None:
    """Test complex scenario with multiple loops and event types."""
    thinking1 = [ThinkingBlock(type="thinking", thinking="First", signature="sig1")]

    msg1 = message_event("Start")

    # Tool loop 1
    action1 = create_action_event("response_1", "call_1", thinking_blocks=thinking1)
    obs1 = create_observation_event("call_1")
    action2 = create_action_event("response_2", "call_2")
    obs2 = create_observation_event("call_2")

    msg2 = message_event("Middle")

    # Regular action without thinking
    action3 = create_action_event("response_3", "call_3")
    obs3 = create_observation_event("call_3")

    msg3 = message_event("End")

    events = [msg1, action1, obs1, action2, obs2, msg2, action3, obs3, msg3]

    prop = ToolLoopAtomicityProperty()
    indices = prop.manipulation_indices(events, events)

    # msg1: 0
    # Loop1: 1-4 (action1, obs1, action2, obs2)
    # msg2: 5
    # action3/obs3: 6-7 (no thinking, not a loop)
    # msg3: 8
    # Can manipulate at: 0, 1, 5, 6, 7, 8, 9
    # Cannot manipulate at: 2, 3, 4 (within loop1)
    assert indices == {0, 1, 5, 6, 7, 8, 9}
