import pytest

from openhands.sdk.context.view import View
from openhands.sdk.event import (
    ActionEvent,
    CondensationRequest,
    MessageEvent,
    ObservationEvent,
    SystemPromptEvent,
)
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation
from openhands.sdk.tool.builtins.new_context import (
    NewContextAction,
    NewContextObservation,
)


@pytest.fixture
def reset_view() -> View:
    action = ActionEvent(
        id="reset-action",
        thought=[],
        action=NewContextAction(handoff="Continue the implementation."),
        tool_name="new_context",
        tool_call_id="reset-call",
        tool_call=MessageToolCall(
            id="reset-call",
            name="new_context",
            arguments='{"handoff":"Continue the implementation."}',
            origin="completion",
        ),
        llm_response_id="reset-response",
    )
    sibling = ActionEvent(
        id="sibling-action",
        thought=[],
        action=MCPToolAction(data={}),
        tool_name="test_tool",
        tool_call_id="sibling-call",
        tool_call=MessageToolCall(
            id="sibling-call", name="test_tool", arguments="{}", origin="completion"
        ),
        llm_response_id=action.llm_response_id,
    )
    return View.from_events(
        [
            SystemPromptEvent(
                id="system", system_prompt=TextContent(text="System"), tools=[]
            ),
            MessageEvent(
                id="old-user",
                source="user",
                llm_message=Message(
                    role="user", content=[TextContent(text="Original")]
                ),
            ),
            MessageEvent(
                id="boundary",
                source="agent",
                llm_message=Message(
                    role="assistant", content=[TextContent(text="Old output " * 1000)]
                ),
                llm_response_id="old-response",
            ),
            MessageEvent(
                id="concurrent-user-1",
                source="user",
                llm_message=Message(
                    role="user", content=[TextContent(text="Also fix A")]
                ),
            ),
            MessageEvent(
                id="concurrent-user-2",
                source="user",
                llm_message=Message(
                    role="user", content=[TextContent(text="Also fix B")]
                ),
            ),
            action,
            sibling,
            ObservationEvent(
                id="reset-result",
                action_id=action.id,
                tool_name=action.tool_name,
                tool_call_id=action.tool_call_id,
                observation=NewContextObservation(
                    content=[TextContent(text="Reset requested")],
                    input_event_id="boundary",
                ),
            ),
            ObservationEvent(
                id="sibling-result",
                action_id=sibling.id,
                tool_name=sibling.tool_name,
                tool_call_id=sibling.tool_call_id,
                observation=MCPToolObservation.from_text(
                    text="Result", tool_name=sibling.tool_name
                ),
            ),
            CondensationRequest(trigger_action_id=action.id),
        ]
    )
