"""Tests for message conversion with dynamic context."""

from typing import cast

import pytest

from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.llm_convertible import MessageEvent, SystemPromptEvent
from openhands.sdk.llm import Message, TextContent


@pytest.mark.parametrize(
    ("dynamic_context", "expected_blocks"),
    [
        (TextContent(text="Working directory: /workspace\nDate: 2024-01-15"), 2),
        (None, 1),
    ],
)
def test_events_to_messages_system_prompt_blocks(dynamic_context, expected_blocks):
    system_event = SystemPromptEvent(
        source="agent",
        system_prompt=TextContent(text="You are a helpful assistant."),
        tools=[],
        dynamic_context=dynamic_context,
    )

    user_message = MessageEvent(
        source="user",
        llm_message=Message(
            role="user",
            content=[TextContent(text="Hi")],
        ),
    )

    events = cast(list[LLMConvertibleEvent], [system_event, user_message])
    messages = LLMConvertibleEvent.events_to_messages(events)

    assert len(messages) == 2
    assert [message.role for message in messages] == ["system", "user"]

    system_message = messages[0]
    assert len(system_message.content) == expected_blocks
    assert isinstance(system_message.content[0], TextContent)
    assert system_message.content[0].text == "You are a helpful assistant."

    if dynamic_context is None:
        assert expected_blocks == 1
    else:
        assert isinstance(system_message.content[1], TextContent)
        assert system_message.content[1].text == dynamic_context.text

    user_msg = messages[1]
    assert len(user_msg.content) == 1
    assert isinstance(user_msg.content[0], TextContent)
    assert user_msg.content[0].text == "Hi"
