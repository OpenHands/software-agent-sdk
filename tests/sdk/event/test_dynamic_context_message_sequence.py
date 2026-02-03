"""Test that dynamic context doesn't create consecutive user messages.

When SystemPromptEvent has dynamic_context and a user message follows,
the message sequence sent to the LLM should NOT have consecutive user messages.

LLMs typically expect alternating user/assistant messages after system prompt.
Two consecutive user messages would break this expectation and could cause
issues with some LLM providers.
"""

from typing import cast

import pytest

from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.llm_convertible import MessageEvent, SystemPromptEvent
from openhands.sdk.llm import Message, TextContent


def test_dynamic_context_followed_by_user_message_no_consecutive_users():
    """Test dynamic_context + user message doesn't create consecutive users.

    This is a regression test for the prompt caching fix where:
    1. SystemPromptEvent emits [system_msg, user_msg] when dynamic_context is present
    2. User sends "Hi" which becomes another user message
    3. The final sequence should NOT have [user, user] consecutive messages

    Expected behavior: The dynamic context should be merged or handled such that
    we don't end up with two consecutive user messages.
    """
    # Create SystemPromptEvent WITH dynamic context
    system_event = SystemPromptEvent(
        source="agent",
        system_prompt=TextContent(text="You are a helpful assistant."),
        tools=[],
        dynamic_context=TextContent(
            text="Working directory: /workspace\nDate: 2024-01-15"
        ),
    )

    # Create user message (what the user types in the UI)
    user_message = MessageEvent(
        source="user",
        llm_message=Message(
            role="user",
            content=[TextContent(text="Hi")],
        ),
    )

    # Convert to LLM messages
    events = cast(list[LLMConvertibleEvent], [system_event, user_message])
    messages = LLMConvertibleEvent.events_to_messages(events)

    # Check for consecutive user messages
    roles = [m.role for m in messages]
    print(f"Message roles: {roles}")  # Debug output

    # There should NOT be consecutive user messages
    for i in range(len(roles) - 1):
        if roles[i] == "user" and roles[i + 1] == "user":
            pytest.fail(
                f"Found consecutive user messages at positions {i} and {i + 1}. "
                f"Full role sequence: {roles}. "
                "This will cause issues with LLMs that expect "
                "alternating user/assistant messages."
            )

    # Verify we have the expected structure:
    # - System message should be first
    # - User message(s) should eventually contain "Hi"
    assert messages[0].role == "system"
    assert any(
        m.role == "user"
        and any(isinstance(c, TextContent) and "Hi" in c.text for c in m.content)
        for m in messages
    ), "User's 'Hi' message should be present in the message sequence"


def test_system_prompt_without_dynamic_context_followed_by_user():
    """Test that system prompt without dynamic_context works correctly.

    This is a baseline test - without dynamic_context, there should be
    no issue with consecutive user messages.
    """
    # Create SystemPromptEvent WITHOUT dynamic context
    system_event = SystemPromptEvent(
        source="agent",
        system_prompt=TextContent(text="You are a helpful assistant."),
        tools=[],
        dynamic_context=None,
    )

    # Create user message
    user_message = MessageEvent(
        source="user",
        llm_message=Message(
            role="user",
            content=[TextContent(text="Hi")],
        ),
    )

    events = cast(list[LLMConvertibleEvent], [system_event, user_message])
    messages = LLMConvertibleEvent.events_to_messages(events)

    roles = [m.role for m in messages]
    print(f"Message roles (no dynamic context): {roles}")

    # Should be: [system, user]
    assert len(messages) == 2
    assert roles == ["system", "user"]


def test_dynamic_context_message_content_preserved():
    """Test that dynamic context content is preserved in the message sequence.

    Even after fixing the consecutive user message issue, we need to ensure
    the dynamic context information is still available to the LLM.
    """
    dynamic_content = (
        "Working directory: /workspace\nDate: 2024-01-15\nUser: test@example.com"
    )

    system_event = SystemPromptEvent(
        source="agent",
        system_prompt=TextContent(text="You are a helpful assistant."),
        tools=[],
        dynamic_context=TextContent(text=dynamic_content),
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

    # Collect all text content
    all_text = []
    for m in messages:
        for c in m.content:
            if isinstance(c, TextContent):
                all_text.append(c.text)

    full_text = "\n".join(all_text)

    # Dynamic context should be present somewhere in the messages
    assert "Working directory" in full_text, "Dynamic context should be preserved"
    assert "Date: 2024-01-15" in full_text, "Dynamic context should be preserved"
    assert "Hi" in full_text, "User message should be preserved"
