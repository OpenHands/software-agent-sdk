"""Reproduction for issue #3045.

Crash recovery can create duplicate observation-like events for the same
tool_call_id (one AgentErrorEvent emitted by event_service restart recovery,
plus a late ObservationEvent for the same action). The duplicate slips past
ToolCallMatchingProperty.enforce() because that method only checks set
membership of tool_call_ids -- it has no cardinality awareness. Then later
ToolCallMatchingProperty.manipulation_indices() walks the events and calls
pending_tool_call_ids.remove(tool_call_id) twice for the same id, which
raises KeyError. That KeyError propagates up through condenser code and
crashes the conversation.

Run from the repo root:
    uv run --frozen python .pr/reproduce_issue_3045.py
"""

from __future__ import annotations

from openhands.sdk.context.view import View
from openhands.sdk.context.view.properties.tool_call_matching import (
    ToolCallMatchingProperty,
)
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation


TOOL_CALL_ID = "toolu_01WK8m53DJP6KkuqVCBSbYzm"  # from the trajectory KeyError
TOOL_NAME = "terminal"


def make_action_event() -> ActionEvent:
    return ActionEvent(
        id="action-1",
        thought=[TextContent(text="run a long pytest command")],
        action=MCPToolAction(data={}),
        tool_name=TOOL_NAME,
        tool_call_id=TOOL_CALL_ID,
        tool_call=MessageToolCall(
            id=TOOL_CALL_ID,
            name=TOOL_NAME,
            arguments="{}",
            origin="completion",
        ),
        llm_response_id="response-1",
        source="agent",
    )


def make_crash_recovery_error() -> AgentErrorEvent:
    """The exact AgentErrorEvent emitted by event_service restart recovery
    when a stale RUNNING conversation is reloaded with an unmatched action.
    """
    return AgentErrorEvent(
        tool_name=TOOL_NAME,
        tool_call_id=TOOL_CALL_ID,
        error=(
            "A restart occurred while this tool was in progress. "
            "This may indicate a fatal memory error or system crash. "
            "The tool execution was interrupted and did not complete."
        ),
    )


def make_late_observation_event() -> ObservationEvent:
    """The late ObservationEvent that arrives AFTER crash recovery already
    decided the tool was lost and synthesized an AgentErrorEvent for it.
    """
    return ObservationEvent(
        id="obs-late",
        observation=MCPToolObservation.from_text(
            text="pytest finished after the restart -- this result is late",
            tool_name=TOOL_NAME,
        ),
        tool_name=TOOL_NAME,
        tool_call_id=TOOL_CALL_ID,
        action_id="action-1",
        source="environment",
    )


def make_user_message() -> MessageEvent:
    return MessageEvent(
        llm_message=Message(
            role="user", content=[TextContent(text="please run the tests")]
        ),
        source="user",
    )


def main() -> None:
    user_msg = make_user_message()
    action = make_action_event()
    crash_recovery_error = make_crash_recovery_error()
    late_observation = make_late_observation_event()

    events = [user_msg, action, crash_recovery_error, late_observation]

    print("=" * 78)
    print(
        "Event sequence "
        "(1 ActionEvent + 2 observation-like events for the SAME tool_call_id):"
    )
    for i, e in enumerate(events):
        tcid = getattr(e, "tool_call_id", "")
        print(f"  [{i}] {type(e).__name__:22s}  tool_call_id={tcid}")
    print()

    prop = ToolCallMatchingProperty()

    # Step 1: enforce() should detect the duplicate observation-like event.
    to_remove = prop.enforce(events, events)
    print(f"ToolCallMatchingProperty.enforce() => events_to_remove = {to_remove}")
    print()

    # Step 2: manipulation_indices() must not raise KeyError on the duplicate.
    print("Calling ToolCallMatchingProperty.manipulation_indices(events)...")
    try:
        prop.manipulation_indices(events)
    except KeyError as e:
        print(f"  -> KeyError: {e!r}  (BUG present)")
        print()
    else:
        print("  -> no KeyError (BUG fixed)")
        print()

    # Step 3: View.from_events runs enforce() then exposes manipulation_indices.
    # This is the path the LLM-summarizing condenser hits in production.
    print("Building View.from_events() and accessing view.manipulation_indices...")
    view = View.from_events(events)
    print(f"  view len = {len(view)}")
    try:
        view.manipulation_indices  # noqa: B018  -- property access triggers the bug
    except KeyError as e:
        print(f"  -> KeyError: {e!r}  (BUG present)")
    else:
        print("  -> no KeyError (BUG fixed)")


if __name__ == "__main__":
    main()
