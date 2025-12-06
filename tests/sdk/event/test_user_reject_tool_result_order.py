from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.llm_convertible import UserRejectObservation
from openhands.sdk.llm import Message, MessageToolCall, TextContent


def test_reject_emits_tool_result_immediately_after_tool_use():
    # 1) Simulate a user message that led to a tool call
    user_msg = MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="do something")]),
    )

    # 2) Simulate assistant tool_use (ActionEvent)
    tool_call = MessageToolCall(
        id="toolu_01ABC",
        name="str_replace_editor",
        arguments="{}",
        origin="completion",
    )
    action = ActionEvent(
        source="agent",
        thought=[TextContent(text="Let me do that")],
        tool_name="str_replace_editor",
        tool_call_id=tool_call.id,
        tool_call=tool_call,
        llm_response_id="resp_1",
        action=None,  # not executed due to confirmation mode
    )

    # 3) Simulate a user rejection of that action
    reject = UserRejectObservation(
        source="environment",
        action_id=action.id,
        tool_name=action.tool_name,
        tool_call_id=action.tool_call_id,
        rejection_reason="Please explain first",
    )

    # Convert events to LLM messages using the core utility
    messages = LLMConvertibleEvent.events_to_messages([user_msg, action, reject])

    # Assert shape: assistant tool_use followed immediately by tool_result
    # messages[0] is user
    assert messages[1].role == "assistant"
    assert messages[1].tool_calls and len(messages[1].tool_calls) == 1
    assert messages[1].tool_calls[0].id == tool_call.id

    assert messages[2].role == "tool"
    assert messages[2].tool_call_id == tool_call.id
    # Ensure serializer is forced to string to satisfy strict providers
    assert getattr(messages[2], "force_string_serializer", False) is True
