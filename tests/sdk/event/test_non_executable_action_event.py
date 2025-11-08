import json
from collections.abc import Sequence

from openhands.sdk.event.llm_convertible import ActionEvent
from openhands.sdk.llm import MessageToolCall, TextContent


def test_action_event_with_none_action_to_llm_message_round_trip() -> None:
    """Test ActionEvent with action=None (non-executable) to_llm_message."""
    thought: Sequence[TextContent] = [TextContent(text="thinking...")]
    tc = MessageToolCall(
        id="call_xyz",
        name="missing_tool",
        arguments=json.dumps({"a": 1}),
        origin="completion",
    )

    evt = ActionEvent(
        source="agent",
        thought=thought,
        reasoning_content="rc",
        thinking_blocks=[],
        tool_call=tc,
        tool_name=tc.name,
        tool_call_id=tc.id,
        llm_response_id="resp_1",
        action=None,
    )

    msg = evt.to_llm_message()
    assert msg.role == "assistant"
    assert msg.tool_calls is not None and len(msg.tool_calls) == 1
    assert msg.tool_calls[0].id == "call_xyz"
    assert msg.tool_calls[0].name == "missing_tool"
    assert len(msg.content) == 1 and isinstance(msg.content[0], TextContent)
    assert msg.content[0].text == "thinking..."
