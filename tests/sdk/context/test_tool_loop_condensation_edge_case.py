"""Test edge case: what happens when condensation is needed but there's only a tool loop?

This tests the scenario where:
- Condensation is triggered (e.g., too many tokens)
- The entire conversation after keep_first is a single tool loop
- The condenser needs to decide: keep the tool loop or remove it entirely
"""

from openhands.sdk.context.view import View
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.llm import (
    Message,
    MessageToolCall,
    TextContent,
    ThinkingBlock,
)
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation


def create_action_event(
    llm_response_id: str,
    tool_call_id: str,
    tool_name: str = "test_tool",
    thinking_blocks: list[ThinkingBlock] | None = None,
) -> ActionEvent:
    """Helper to create an ActionEvent."""
    action = MCPToolAction(data={})
    tool_call = MessageToolCall(
        id=tool_call_id,
        name=tool_name,
        arguments="{}",
        origin="completion",
    )

    return ActionEvent(
        thought=[TextContent(text="Test thought")],
        thinking_blocks=list(thinking_blocks) if thinking_blocks else [],  # type: ignore[arg-type]
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


def test_condensation_with_only_tool_loop_after_keep_first():
    """Test the edge case where everything after keep_first is a tool loop.

    This simulates the integration test scenario where:
    - keep_first = 2
    - After the first 2 events, there's a single long tool loop
    - Condensation needs to happen but can't split the tool loop
    """
    thinking = [
        ThinkingBlock(
            type="thinking",
            thinking="Extended thinking...",
            signature="sig",
        )
    ]

    events = [
        message_event("User message"),
        message_event("Another user message"),
        # Single long tool loop starts here (index 2)
        create_action_event("resp_1", "call_1", thinking_blocks=thinking),
        create_observation_event("call_1"),
        create_action_event("resp_2", "call_2"),
        create_observation_event("call_2"),
        create_action_event("resp_3", "call_3"),
        create_observation_event("call_3"),
        create_action_event("resp_4", "call_4"),
        create_observation_event("call_4"),
        create_action_event("resp_5", "call_5"),
        create_observation_event("call_5"),
    ]

    view = View.from_events(events)
    indices = view.manipulation_indices

    print(f"View length: {len(view.events)}")
    print(f"Manipulation indices: {indices}")

    # With keep_first = 2, we want to keep events [0, 1]
    # and condense everything after
    keep_first = 2

    # Find where forgetting should start
    forgetting_start = view.find_next_manipulation_index(keep_first, strict=True)
    print(f"forgetting_start (after keep_first={keep_first}): {forgetting_start}")

    # If we want to condense to remove 50% of events
    naive_end = len(view) // 2 + keep_first
    print(f"naive_end (50% reduction): {naive_end}")

    forgetting_end = view.find_next_manipulation_index(naive_end, strict=False)
    print(f"forgetting_end: {forgetting_end}")

    # This shows the problem: forgetting_start will be at the end of the tool loop
    # because the tool loop is atomic and starts at index 2
    # Expected: indices = [0, 1, 2, 12]
    # - forgetting_start after keep_first=2 with strict=True should be 12
    # - forgetting_end would also be 12 or later
    # - So we can't actually condense anything!

    assert indices == [0, 1, 2, 12], f"Expected [0, 1, 2, 12], got {indices}"
    assert forgetting_start == 12, f"Expected forgetting_start=12, got {forgetting_start}"

    # The issue: we can't condense part of the tool loop
    # The only option is to condense the entire tool loop or none of it


def test_condensation_works_with_tool_loop_boundaries():
    """Test that condensation CAN work when tool loops align with boundaries.

    This shows a case where condensation can successfully happen because
    there are multiple tool loops separated by user messages.
    """
    thinking = [
        ThinkingBlock(
            type="thinking",
            thinking="Extended thinking...",
            signature="sig",
        )
    ]

    events = [
        message_event("User 1"),
        message_event("User 2"),
        # First tool loop
        create_action_event("resp_1", "call_1", thinking_blocks=thinking),
        create_observation_event("call_1"),
        create_action_event("resp_2", "call_2"),
        create_observation_event("call_2"),
        message_event("User 3"),
        # Second tool loop
        create_action_event("resp_3", "call_3", thinking_blocks=thinking),
        create_observation_event("call_3"),
        create_action_event("resp_4", "call_4"),
        create_observation_event("call_4"),
        message_event("User 4"),
    ]

    view = View.from_events(events)
    indices = view.manipulation_indices

    print(f"\nView length: {len(view.events)}")
    print(f"Manipulation indices: {indices}")

    keep_first = 2

    # Find forgetting boundaries
    forgetting_start = view.find_next_manipulation_index(keep_first, strict=True)
    print(f"forgetting_start: {forgetting_start}")

    # Want to keep last 4 events (second tool loop + user 4)
    naive_end = len(view) - 4
    forgetting_end = view.find_next_manipulation_index(naive_end, strict=False)
    print(f"naive_end: {naive_end}, forgetting_end: {forgetting_end}")

    # Expected: indices = [0, 1, 2, 6, 7, 11, 12]
    # forgetting_start with keep_first=2, strict=True is the next index after 2, which is 6
    assert forgetting_start == 6
    # Can successfully condense between boundaries [6:11] (the middle user message + second tool loop)
    # This will work because we have proper boundaries!


if __name__ == "__main__":
    test_condensation_with_only_tool_loop_after_keep_first()
    test_condensation_works_with_tool_loop_boundaries()
    print("\nAll edge case tests passed!")
