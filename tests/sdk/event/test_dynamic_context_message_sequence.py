"""Test that dynamic context is handled via cache breakpoints in system message.

When SystemPromptEvent has dynamic_context, it should be included as a second
content block inside the system message. Cache markers are NOT applied by
SystemPromptEvent - they are applied by LLM._apply_prompt_caching() when
caching is enabled. This ensures provider-specific cache control is only
added when appropriate.
"""

from typing import cast

from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.llm_convertible import MessageEvent, SystemPromptEvent
from openhands.sdk.llm import Message, TextContent


def test_dynamic_context_produces_two_system_content_blocks():
    """Dynamic context should appear as a second content block in the system message."""
    system_event = SystemPromptEvent(
        source="agent",
        system_prompt=TextContent(text="You are a helpful assistant."),
        tools=[],
        dynamic_context=TextContent(
            text="Working directory: /workspace\nDate: 2024-01-15"
        ),
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

    # Should be [system, user] — no extra messages
    assert len(messages) == 2
    roles = [m.role for m in messages]
    assert roles == ["system", "user"]

    # System message should have 2 content blocks
    sys_msg = messages[0]
    assert len(sys_msg.content) == 2
    assert isinstance(sys_msg.content[0], TextContent)
    assert isinstance(sys_msg.content[1], TextContent)
    assert sys_msg.content[0].text == "You are a helpful assistant."
    assert "Working directory" in sys_msg.content[1].text

    # User message should be unmodified
    user_msg = messages[1]
    assert len(user_msg.content) == 1
    assert isinstance(user_msg.content[0], TextContent)
    assert user_msg.content[0].text == "Hi"


def test_system_prompt_without_dynamic_context_followed_by_user():
    """System prompt without dynamic_context produces a single content block."""
    system_event = SystemPromptEvent(
        source="agent",
        system_prompt=TextContent(text="You are a helpful assistant."),
        tools=[],
        dynamic_context=None,
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
    assert [m.role for m in messages] == ["system", "user"]
    assert len(messages[0].content) == 1


def test_dynamic_context_content_preserved_in_system_message():
    """Dynamic context text must be preserved inside the system message."""
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

    # Dynamic context should be in the system message, not in the user message
    sys_content_texts = [
        c.text for c in messages[0].content if isinstance(c, TextContent)
    ]
    assert any("Working directory" in t for t in sys_content_texts)
    assert any("Date: 2024-01-15" in t for t in sys_content_texts)

    # User message should only contain the user's text
    user_content_texts = [
        c.text for c in messages[1].content if isinstance(c, TextContent)
    ]
    assert user_content_texts == ["Hi"]


def test_to_llm_message_does_not_set_cache_markers():
    """SystemPromptEvent.to_llm_message() should NOT set cache markers.

    Cache markers are applied by LLM._apply_prompt_caching() when caching is
    enabled, not by the event itself. This ensures provider-specific cache
    control is only added when appropriate.
    """
    system_event = SystemPromptEvent(
        source="agent",
        system_prompt=TextContent(text="Static prompt"),
        tools=[],
        dynamic_context=TextContent(text="Dynamic context"),
    )

    msg = system_event.to_llm_message()

    assert len(msg.content) == 2
    # Neither block should have cache markers set by to_llm_message()
    assert isinstance(msg.content[0], TextContent)
    assert msg.content[0].text == "Static prompt"
    assert msg.content[0].cache_prompt is False  # Default value, not set

    assert isinstance(msg.content[1], TextContent)
    assert msg.content[1].text == "Dynamic context"
    assert msg.content[1].cache_prompt is False  # Default value, not set


def test_to_llm_message_single_block_no_cache_marker():
    """Single block should also not have cache marker set by to_llm_message()."""
    system_event = SystemPromptEvent(
        source="agent",
        system_prompt=TextContent(text="Static prompt"),
        tools=[],
        dynamic_context=None,
    )

    msg = system_event.to_llm_message()

    assert len(msg.content) == 1
    assert isinstance(msg.content[0], TextContent)
    assert msg.content[0].text == "Static prompt"
    assert msg.content[0].cache_prompt is False  # Default value, not set


def test_apply_prompt_caching_sets_markers_on_two_block_system():
    """LLM._apply_prompt_caching() must mark static block, leave dynamic unmarked.

    This test verifies the actual caching behavior by calling _apply_prompt_caching()
    on messages created from SystemPromptEvent.to_llm_message(). The static block
    (index 0) should be marked with cache_prompt=True, while the dynamic block
    (index 1) should remain cache_prompt=False for cross-conversation cache sharing.
    """
    from pydantic import SecretStr

    from openhands.sdk import LLM

    llm = LLM(
        model="claude-sonnet-4-20250514",
        api_key=SecretStr("fake-key"),
        usage_id="test",
        caching_prompt=True,
    )

    # Create SystemPromptEvent with dynamic context
    system_event = SystemPromptEvent(
        source="agent",
        system_prompt=TextContent(text="Static system prompt"),
        tools=[],
        dynamic_context=TextContent(text="Dynamic context: hosts, repos, etc."),
    )

    user_message = MessageEvent(
        source="user",
        llm_message=Message(
            role="user",
            content=[TextContent(text="Hello")],
        ),
    )

    # Convert events to messages
    events = cast(list[LLMConvertibleEvent], [system_event, user_message])
    messages = LLMConvertibleEvent.events_to_messages(events)

    # Verify initial state: no cache markers set
    assert messages[0].content[0].cache_prompt is False
    assert messages[0].content[1].cache_prompt is False

    # Apply prompt caching
    llm._apply_prompt_caching(messages)

    # Verify cache markers are correctly set
    assert messages[0].content[0].cache_prompt is True, (
        "Static block (index 0) should be marked for caching"
    )
    assert messages[0].content[1].cache_prompt is False, (
        "Dynamic block (index 1) should NOT be marked for caching"
    )
    # User message's last content should also be marked
    assert messages[1].content[-1].cache_prompt is True


def test_apply_prompt_caching_sets_marker_on_single_block_system():
    """LLM._apply_prompt_caching() must mark single system block.

    When there's only one content block in the system message (no dynamic context),
    that block should be marked with cache_prompt=True.
    """
    from pydantic import SecretStr

    from openhands.sdk import LLM

    llm = LLM(
        model="claude-sonnet-4-20250514",
        api_key=SecretStr("fake-key"),
        usage_id="test",
        caching_prompt=True,
    )

    # Create SystemPromptEvent without dynamic context
    system_event = SystemPromptEvent(
        source="agent",
        system_prompt=TextContent(text="Static system prompt only"),
        tools=[],
        dynamic_context=None,
    )

    user_message = MessageEvent(
        source="user",
        llm_message=Message(
            role="user",
            content=[TextContent(text="Hello")],
        ),
    )

    # Convert events to messages
    events = cast(list[LLMConvertibleEvent], [system_event, user_message])
    messages = LLMConvertibleEvent.events_to_messages(events)

    # Verify initial state: no cache marker set
    assert messages[0].content[0].cache_prompt is False

    # Apply prompt caching
    llm._apply_prompt_caching(messages)

    # Verify cache marker is set on the single block
    assert messages[0].content[0].cache_prompt is True, (
        "Single system block should be marked for caching"
    )
    # User message's last content should also be marked
    assert messages[1].content[-1].cache_prompt is True
