import json

from openhands.sdk.context.view import View
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
)
from openhands.sdk.llm import Message, MessageToolCall, TextContent


def test_filter_keeps_action_none_when_matched_by_observation() -> None:
    """Test that ActionEvent with action=None is kept when matched by observation."""
    # ActionEvent with action=None and a tool_call id
    tc = MessageToolCall(
        id="call_keep_me",
        name="missing_tool",
        arguments=json.dumps({}),
        origin="completion",
    )
    action_event = ActionEvent(
        source="agent",
        thought=[TextContent(text="...")],
        tool_call=tc,
        tool_name=tc.name,
        tool_call_id=tc.id,
        llm_response_id="resp_view_1",
        action=None,
    )

    # Matching AgentErrorEvent (observation path)
    err = AgentErrorEvent(
        source="agent",
        error="not found",
        tool_name="missing_tool",
        tool_call_id="call_keep_me",
    )

    # Noise message events
    m1 = MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="hi")]),
    )
    m2 = MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="bye")]),
    )

    events = [m1, action_event, err, m2]

    filtered = View._filter_unmatched_tool_calls(events, events)  # type: ignore[arg-type]

    # Both ActionEvent(action=None) and matching AgentErrorEvent must be kept
    assert len(filtered) == 4
    assert action_event in filtered
    assert err in filtered
    assert m1 in filtered and m2 in filtered
