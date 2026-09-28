from unittest.mock import patch

import pytest
from litellm.types.utils import Message as LiteLLMMessage

from openhands.sdk.llm.message import Message


# Default serialization options for to_chat_dict() - tests can override as needed
DEFAULT_SERIALIZATION_OPTS = {
    "cache_enabled": False,
    "vision_enabled": False,
    "function_calling_enabled": False,
    "force_string_serializer": False,
    "send_reasoning_content": False,
}


def test_content_base_class_not_implemented():
    """Test that Content base class cannot be instantiated due to abstract method."""
    from openhands.sdk.llm.message import BaseContent

    with pytest.raises(TypeError, match="Can't instantiate abstract class BaseContent"):
        BaseContent()  # type: ignore[abstract]


def test_text_content_with_cache_prompt():
    """Test TextContent with cache_prompt enabled."""
    from openhands.sdk.llm.message import TextContent

    content = TextContent(text="Hello world", cache_prompt=True)
    result = content.to_llm_dict()

    assert len(result) == 1
    assert result[0]["type"] == "text"
    assert result[0]["text"] == "Hello world"
    assert result[0]["cache_control"] == {"type": "ephemeral"}


def test_image_content_with_cache_prompt():
    """Test ImageContent with cache_prompt enabled."""
    from openhands.sdk.llm.message import ImageContent

    content = ImageContent(
        image_urls=["data:image/png;base64,abc123", "data:image/jpeg;base64,def456"],
        cache_prompt=True,
    )
    result = content.to_llm_dict()

    assert len(result) == 2
    assert result[0]["type"] == "image_url"
    assert result[0]["image_url"]["url"] == "data:image/png;base64,abc123"  # type: ignore
    assert result[1]["type"] == "image_url"
    assert result[1]["image_url"]["url"] == "data:image/jpeg;base64,def456"  # type: ignore
    # Only the last image should have cache_control
    assert "cache_control" not in result[0]
    assert result[1]["cache_control"] == {"type": "ephemeral"}


def test_message_contains_image_property():
    """Test Message.contains_image property."""
    from openhands.sdk.llm.message import ImageContent, Message, TextContent

    # Message with only text content
    text_message = Message(role="user", content=[TextContent(text="Hello")])
    assert not text_message.contains_image

    # Message with image content
    image_message = Message(
        role="user",
        content=[
            TextContent(text="Look at this:"),
            ImageContent(
                image_urls=["data:image/png;base64,abc123"],
            ),
        ],
    )
    assert image_message.contains_image


def test_message_tool_role_with_cache_prompt():
    """Test Message with tool role and cache_prompt."""
    from openhands.sdk.llm.message import Message, TextContent

    message = Message(
        role="tool",
        content=[TextContent(text="Tool response", cache_prompt=True)],
        tool_call_id="call_123",
        name="test_tool",
    )

    result = message.to_chat_dict(
        **{**DEFAULT_SERIALIZATION_OPTS, "cache_enabled": True}
    )
    assert result["role"] == "tool"
    assert result["tool_call_id"] == "call_123"
    assert result["cache_control"] == {"type": "ephemeral"}
    # The content should not have cache_control since it's moved to message level
    assert "cache_control" not in result["content"][0]


def test_message_tool_role_with_image_cache_prompt():
    """Test Message with tool role and ImageContent with cache_prompt."""
    from openhands.sdk.llm.message import ImageContent, Message

    message = Message(
        role="tool",
        content=[
            ImageContent(
                image_urls=["data:image/png;base64,abc123"],
                cache_prompt=True,
            )
        ],
        tool_call_id="call_123",
        name="test_tool",
    )

    result = message.to_chat_dict(
        **{**DEFAULT_SERIALIZATION_OPTS, "vision_enabled": True, "cache_enabled": True}
    )
    assert result["role"] == "tool"
    assert result["tool_call_id"] == "call_123"
    assert result["cache_control"] == {"type": "ephemeral"}
    # The image content should not have cache_control since it's moved to message level
    assert "cache_control" not in result["content"][0]


def test_message_with_tool_calls():
    """Test Message with tool_calls."""
    from openhands.sdk.llm.message import (
        Message,
        MessageToolCall,
        TextContent,
    )

    tool_call = MessageToolCall(
        id="call_123",
        name="test_function",
        arguments='{"arg": "value"}',
        origin="completion",
    )

    message = Message(
        role="assistant",
        content=[TextContent(text="I'll call a function")],
        tool_calls=[tool_call],
    )

    result = message.to_chat_dict(**DEFAULT_SERIALIZATION_OPTS)
    assert result["role"] == "assistant"
    assert "tool_calls" in result
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["id"] == "call_123"
    assert result["tool_calls"][0]["type"] == "function"
    assert result["tool_calls"][0]["function"]["name"] == "test_function"
    assert result["tool_calls"][0]["function"]["arguments"] == '{"arg": "value"}'


def test_message_tool_calls_drop_empty_string_content():
    """Assistant tool calls with no text should not include empty content strings."""
    from openhands.sdk.llm.message import Message, MessageToolCall

    tool_call = MessageToolCall(
        id="call_empty",
        name="test_function",
        arguments="{}",
        origin="completion",
    )

    message = Message(
        role="assistant",
        content=[],
        tool_calls=[tool_call],
    )

    result = message.to_chat_dict(**DEFAULT_SERIALIZATION_OPTS)
    assert "content" not in result


def test_message_tool_calls_strip_blank_list_content():
    """List-serialized tool call messages should drop blank text content blocks."""
    from openhands.sdk.llm.message import Message, MessageToolCall, TextContent

    tool_call = MessageToolCall(
        id="call_blank_list",
        name="test_function",
        arguments="{}",
        origin="completion",
    )

    message = Message(
        role="assistant",
        content=[TextContent(text="")],
        tool_calls=[tool_call],
    )

    result = message.to_chat_dict(
        **{**DEFAULT_SERIALIZATION_OPTS, "function_calling_enabled": True}
    )
    assert "content" not in result


def test_empty_assistant_message_uses_string_content_in_list_serializer():
    """List-serialized empty assistant messages should keep string content."""
    message = Message(role="assistant", content=[])

    result = message.to_chat_dict(
        **{**DEFAULT_SERIALIZATION_OPTS, "function_calling_enabled": True}
    )

    assert result == {"content": "", "role": "assistant"}


def test_empty_assistant_from_llm_chat_message_uses_string_content_in_list_serializer():
    """Empty assistant LLM responses should keep string content."""
    litellm_message = LiteLLMMessage(role="assistant", content=None)
    message = Message.from_llm_chat_message(litellm_message)

    result = message.to_chat_dict(
        **{**DEFAULT_SERIALIZATION_OPTS, "function_calling_enabled": True}
    )

    assert result == {"content": "", "role": "assistant"}


def test_message_from_llm_chat_message_function_role_error():
    """Test Message.from_llm_chat_message with function role raises error."""
    from openhands.sdk.llm.message import Message

    litellm_message = LiteLLMMessage(role="function", content="Function response")  # type: ignore

    with pytest.raises(AssertionError, match="Function role is not supported"):
        Message.from_llm_chat_message(litellm_message)


def test_message_from_llm_chat_message_with_non_string_content():
    """Test Message.from_llm_chat_message with non-string content."""
    from openhands.sdk.llm.message import Message

    # Create a message with non-string content (None or list)
    litellm_message = LiteLLMMessage(role="assistant", content=None)

    result = Message.from_llm_chat_message(litellm_message)
    assert result.role == "assistant"
    assert result.content == []  # Empty list for non-string content


def test_text_content_truncation_under_limit():
    """Test TextContent doesn't truncate when under limit."""
    from openhands.sdk.llm.message import TextContent

    content = TextContent(text="Short text")
    result = content.to_llm_dict()

    assert len(result) == 1
    assert result[0]["text"] == "Short text"


def test_text_content_no_truncation_over_limit():
    """TextContent itself should not truncate; truncation is role=tool only."""
    from openhands.sdk.llm.message import TextContent
    from openhands.sdk.utils import DEFAULT_TEXT_CONTENT_LIMIT

    long_text = "A" * (DEFAULT_TEXT_CONTENT_LIMIT + 1000)

    with patch("openhands.sdk.llm.message.logger") as mock_logger:
        content = TextContent(text=long_text)
        result = content.to_llm_dict()

        mock_logger.warning.assert_not_called()
        assert len(result) == 1
        assert result[0]["text"] == long_text


def test_tool_message_truncates_text_over_limit():
    """Tool-role messages should truncate huge TextContent blocks."""
    from openhands.sdk.llm.message import Message, TextContent
    from openhands.sdk.utils import DEFAULT_TEXT_CONTENT_LIMIT

    long_text = "A" * (DEFAULT_TEXT_CONTENT_LIMIT + 1000)

    with patch("openhands.sdk.llm.message.logger") as mock_logger:
        msg = Message(role="tool", content=[TextContent(text=long_text)])
        result = msg.to_chat_dict(
            cache_enabled=True,
            vision_enabled=False,
            function_calling_enabled=False,
            force_string_serializer=False,
            send_reasoning_content=False,
        )

        mock_logger.warning.assert_called_once()
        args = mock_logger.warning.call_args[0]
        assert "Tool TextContent text length" in args[0]
        assert args[1] == DEFAULT_TEXT_CONTENT_LIMIT + 1000
        assert args[2] == DEFAULT_TEXT_CONTENT_LIMIT

        content_item = result["content"][0]
        assert content_item["type"] == "text"
        text_result = content_item["text"]
        assert isinstance(text_result, str)
        assert len(text_result) == DEFAULT_TEXT_CONTENT_LIMIT
        assert "<response clipped>" in text_result


def test_user_message_does_not_truncate_text_over_limit():
    """User-role messages should not truncate at serialization."""
    from openhands.sdk.llm.message import Message, TextContent
    from openhands.sdk.utils import DEFAULT_TEXT_CONTENT_LIMIT

    long_text = "A" * (DEFAULT_TEXT_CONTENT_LIMIT + 1000)

    with patch("openhands.sdk.llm.message.logger") as mock_logger:
        msg = Message(role="user", content=[TextContent(text=long_text)])
        result = msg.to_chat_dict(
            cache_enabled=False,
            vision_enabled=False,
            function_calling_enabled=False,
            force_string_serializer=True,
            send_reasoning_content=False,
        )

        mock_logger.warning.assert_not_called()
        assert result["content"] == long_text


def test_tool_message_truncates_text_over_limit_with_string_serializer():
    """Tool-role truncation must also apply on the string-serializer path."""
    from openhands.sdk.llm.message import Message, TextContent
    from openhands.sdk.utils import DEFAULT_TEXT_CONTENT_LIMIT

    long_text = "A" * (DEFAULT_TEXT_CONTENT_LIMIT + 1000)

    with patch("openhands.sdk.llm.message.logger") as mock_logger:
        msg = Message(role="tool", content=[TextContent(text=long_text)])
        result = msg.to_chat_dict(
            cache_enabled=False,
            vision_enabled=False,
            function_calling_enabled=False,
            force_string_serializer=True,
            send_reasoning_content=False,
        )

        mock_logger.warning.assert_called_once()
        assert result["content"] != long_text
        assert len(result["content"]) == DEFAULT_TEXT_CONTENT_LIMIT
        assert "<response clipped>" in result["content"]


def test_text_content_truncation_exact_limit():
    """Test TextContent doesn't truncate when exactly at limit."""
    from openhands.sdk.llm.message import TextContent
    from openhands.sdk.utils import DEFAULT_TEXT_CONTENT_LIMIT

    # Create text that is exactly at the limit
    exact_text = "A" * DEFAULT_TEXT_CONTENT_LIMIT

    with patch("openhands.sdk.llm.message.logger") as mock_logger:
        content = TextContent(text=exact_text)
        result = content.to_llm_dict()

        # Check that no warning was logged
        mock_logger.warning.assert_not_called()

        # Check that text was not truncated
        assert len(result) == 1
        assert result[0]["text"] == exact_text


def test_message_with_reasoning_content_when_enabled():
    """Test that reasoning_content is included when send_reasoning_content is True."""
    from openhands.sdk.llm.message import Message, TextContent

    message = Message(
        role="assistant",
        content=[TextContent(text="Final answer")],
        reasoning_content="Let me think step by step...",
    )

    result = message.to_chat_dict(
        **{**DEFAULT_SERIALIZATION_OPTS, "send_reasoning_content": True}
    )
    assert result["role"] == "assistant"
    assert result["content"] == "Final answer"
    assert result["reasoning_content"] == "Let me think step by step..."


def test_message_with_reasoning_content_when_disabled():
    """Test that reasoning_content is NOT included when send_reasoning_content is False."""  # noqa: E501
    from openhands.sdk.llm.message import Message, TextContent

    message = Message(
        role="assistant",
        content=[TextContent(text="Final answer")],
        reasoning_content="Let me think step by step...",
    )

    result = message.to_chat_dict(**DEFAULT_SERIALIZATION_OPTS)
    assert result["role"] == "assistant"
    assert result["content"] == "Final answer"
    assert "reasoning_content" not in result


def test_message_with_reasoning_content_default_disabled():
    """Test that reasoning_content is NOT included when send_reasoning_content=False."""
    from openhands.sdk.llm.message import Message, TextContent

    message = Message(
        role="assistant",
        content=[TextContent(text="Final answer")],
        reasoning_content="Let me think step by step...",
    )

    result = message.to_chat_dict(**DEFAULT_SERIALIZATION_OPTS)
    assert result["role"] == "assistant"
    assert result["content"] == "Final answer"
    assert "reasoning_content" not in result


def test_message_with_reasoning_content_none():
    """Test that reasoning_content is NOT included when it's None even if enabled."""
    from openhands.sdk.llm.message import Message, TextContent

    message = Message(
        role="assistant",
        content=[TextContent(text="Final answer")],
        reasoning_content=None,
    )

    result = message.to_chat_dict(
        **{**DEFAULT_SERIALIZATION_OPTS, "send_reasoning_content": True}
    )
    assert result["role"] == "assistant"
    assert result["content"] == "Final answer"
    assert "reasoning_content" not in result


def test_message_with_reasoning_content_empty_string():
    """Test that reasoning_content is NOT included when it's an empty string."""
    from openhands.sdk.llm.message import Message, TextContent

    message = Message(
        role="assistant",
        content=[TextContent(text="Final answer")],
        reasoning_content="",
    )

    result = message.to_chat_dict(
        **{**DEFAULT_SERIALIZATION_OPTS, "send_reasoning_content": True}
    )
    assert result["role"] == "assistant"
    assert result["content"] == "Final answer"
    assert "reasoning_content" not in result


def test_message_with_reasoning_content_list_serializer():
    """Test that reasoning_content works with list serializer."""
    from openhands.sdk.llm.message import Message, TextContent

    message = Message(
        role="assistant",
        content=[TextContent(text="Final answer")],
        reasoning_content="Step by step reasoning",
    )

    result = message.to_chat_dict(
        **{
            **DEFAULT_SERIALIZATION_OPTS,
            "function_calling_enabled": True,  # Forces list serializer
            "send_reasoning_content": True,
        }
    )
    assert result["role"] == "assistant"
    assert isinstance(result["content"], list)
    assert result["content"][0]["text"] == "Final answer"
    assert result["reasoning_content"] == "Step by step reasoning"


# Confirmed bug reproducer: an OpenRouter assistant turn with only opaque
# encrypted reasoning_details (no reasoning_content/thinking_blocks) must
# survive Message.from_llm_chat_message and the outgoing wire dict.
OPAQUE_REASONING_DETAIL = {
    "type": "reasoning.encrypted",
    "data": "opaque-test",
    "format": "anthropic-claude-v1",
    "index": 0,
}


def test_message_from_llm_chat_message_preserves_reasoning_details():
    """LiteLLM's reasoning_details (OpenRouter) must not be dropped.

    Regression test for the reported bug: a LiteLLMMessage carrying only
    ``reasoning_details`` (no reasoning_content, no thinking_blocks) used to
    lose that structured data when converted via
    ``Message.from_llm_chat_message``.
    """
    litellm_message = LiteLLMMessage(
        role="assistant",
        content=None,
        reasoning_details=[OPAQUE_REASONING_DETAIL],
    )

    message = Message.from_llm_chat_message(litellm_message)

    assert message.reasoning_details == [OPAQUE_REASONING_DETAIL]


def test_message_with_reasoning_details_sent_when_enabled():
    """reasoning_details is included in the outgoing dict for OpenRouter."""
    message = Message(
        role="assistant",
        content=[],
        reasoning_details=[OPAQUE_REASONING_DETAIL],
    )

    result = message.to_chat_dict(
        **{**DEFAULT_SERIALIZATION_OPTS, "send_reasoning_details": True}
    )

    assert result["reasoning_details"] == [OPAQUE_REASONING_DETAIL]


def test_message_with_reasoning_details_omitted_by_default():
    """reasoning_details must never leak to non-OpenRouter transports."""
    message = Message(
        role="assistant",
        content=[],
        reasoning_details=[OPAQUE_REASONING_DETAIL],
    )

    # DEFAULT_SERIALIZATION_OPTS does not set send_reasoning_details, so the
    # default (False) applies - the appropriate behavior for every
    # non-OpenRouter transport.
    result = message.to_chat_dict(**DEFAULT_SERIALIZATION_OPTS)

    assert "reasoning_details" not in result


def test_message_with_reasoning_details_none_not_sent():
    """reasoning_details is omitted when None even if the flag is enabled."""
    message = Message(role="assistant", content=[], reasoning_details=None)

    result = message.to_chat_dict(
        **{**DEFAULT_SERIALIZATION_OPTS, "send_reasoning_details": True}
    )

    assert "reasoning_details" not in result


def test_reasoning_details_survives_openrouter_tool_call_round_trip():
    """LiteLLM boundary fixture (no network): opaque reasoning_details must
    survive a full incoming -> outgoing round trip alongside a tool call.

    Simulates OpenRouter's real Chat Completions shape: an assistant turn
    with both a tool call and encrypted reasoning_details (interleaved
    thinking), exactly as LiteLLM normalizes it onto ``litellm.types.utils.
    Message``. This exercises the actual LiteLLM message boundary rather
    than a hand-rolled dict, with no network call involved.
    """
    from litellm import ChatCompletionMessageToolCall
    from litellm.types.utils import Function

    tool_call = ChatCompletionMessageToolCall(
        id="call_abc",
        type="function",
        function=Function(name="get_weather", arguments='{"city": "SF"}'),
    )
    litellm_message = LiteLLMMessage(
        role="assistant",
        content=None,
        tool_calls=[tool_call],
        reasoning_details=[OPAQUE_REASONING_DETAIL],
    )

    # Incoming: LiteLLM boundary -> our Message.
    message = Message.from_llm_chat_message(litellm_message)
    assert message.reasoning_details == [OPAQUE_REASONING_DETAIL]
    assert message.tool_calls is not None
    assert message.tool_calls[0].id == "call_abc"

    # Outgoing: our Message -> OpenRouter wire dict. reasoning_details must
    # be sent back verbatim, and the tool call must survive alongside it.
    result = message.to_chat_dict(
        **{
            **DEFAULT_SERIALIZATION_OPTS,
            "function_calling_enabled": True,
            "send_reasoning_details": True,
        }
    )
    assert result["reasoning_details"] == [OPAQUE_REASONING_DETAIL]
    assert result["tool_calls"][0]["id"] == "call_abc"
    assert result["tool_calls"][0]["function"]["name"] == "get_weather"

    # Same message through a non-OpenRouter transport must drop the
    # OpenRouter-only field while still sending the tool call.
    non_openrouter_result = message.to_chat_dict(
        **{**DEFAULT_SERIALIZATION_OPTS, "function_calling_enabled": True}
    )
    assert "reasoning_details" not in non_openrouter_result
    assert non_openrouter_result["tool_calls"][0]["id"] == "call_abc"


def test_message_deprecated_fields_silently_removed():
    """Test that deprecated fields are silently removed without warnings.

    Deprecated fields are kept permanently for backward compatibility and
    are silently removed (no warnings) to avoid noise when loading old events.
    """
    from openhands.sdk.llm.message import Message

    deprecated_fields = [
        "cache_enabled",
        "vision_enabled",
        "function_calling_enabled",
        "force_string_serializer",
        "send_reasoning_content",
    ]

    # Test each deprecated field individually - should load without error
    for field in deprecated_fields:
        message = Message.model_validate(
            {"role": "user", "content": "test", field: True}
        )
        # The message should be created successfully
        assert message.role == "user"
        # The deprecated field should not exist on the model
        assert not hasattr(message, field)


def test_message_deprecated_fields_are_ignored():
    """Test that deprecated fields are ignored and don't affect the Message."""
    from openhands.sdk.llm.message import Message

    # Use model_validate to pass extra fields that pyright doesn't know about
    message = Message.model_validate(
        {
            "role": "user",
            "content": "test",
            "cache_enabled": True,
            "vision_enabled": True,
            "function_calling_enabled": True,
            "force_string_serializer": True,
            "send_reasoning_content": True,
        }
    )

    # The message should be created successfully
    assert message.role == "user"
    assert len(message.content) == 1

    # The deprecated fields should not exist on the model
    assert not hasattr(message, "cache_enabled")
    assert not hasattr(message, "vision_enabled")
    assert not hasattr(message, "function_calling_enabled")
    assert not hasattr(message, "force_string_serializer")
    assert not hasattr(message, "send_reasoning_content")


def test_text_content_deprecated_enable_truncation_silently_removed():
    """Test deprecated enable_truncation field is silently removed.

    This ensures backward compatibility when loading old events that contain
    the deprecated enable_truncation field. The field is silently removed
    (no warnings) to avoid noise when loading old events.
    """
    from openhands.sdk.llm.message import TextContent

    content = TextContent.model_validate(
        {"type": "text", "text": "Hello world", "enable_truncation": True}
    )

    # The content should be created successfully
    assert content.text == "Hello world"
    assert content.type == "text"
    # The deprecated field should not exist on the model
    assert not hasattr(content, "enable_truncation")


def test_text_content_old_format_with_enable_truncation_loads_successfully():
    """Test that old event format with enable_truncation loads without error.

    This simulates loading an old event that was persisted before the field
    was deprecated. The event should load successfully and the deprecated
    field should be ignored.
    """
    import warnings

    from openhands.sdk.llm.message import TextContent

    # Simulate the JSON structure of an old event
    old_event_text_content = {
        "type": "text",
        "text": "Tool execution result",
        "cache_prompt": False,
        "enable_truncation": True,  # Old deprecated field
    }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # Suppress warnings for this test
        content = TextContent.model_validate(old_event_text_content)

    # Should load successfully
    assert content.text == "Tool execution result"
    assert content.type == "text"
    assert content.cache_prompt is False


def test_text_content_both_old_and_new_format_in_sequence():
    """Test that both old and new format TextContent can be loaded in sequence.

    This simulates a scenario where we're loading a conversation that contains
    events from different SDK versions - some with deprecated fields and some
    without.
    """
    import warnings

    from openhands.sdk.llm.message import TextContent

    # Simulate loading multiple events from different SDK versions
    event_contents = [
        # Old format (with deprecated field)
        {"type": "text", "text": "Old event 1", "enable_truncation": True},
        # New format
        {"type": "text", "text": "New event 1"},
        # Old format (with deprecated field and cache_prompt)
        {
            "type": "text",
            "text": "Old event 2",
            "enable_truncation": False,
            "cache_prompt": True,
        },
        # New format with cache_prompt
        {"type": "text", "text": "New event 2", "cache_prompt": True},
    ]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # Suppress warnings for this test
        loaded_contents = [TextContent.model_validate(ec) for ec in event_contents]

    # All should load successfully
    assert len(loaded_contents) == 4
    assert loaded_contents[0].text == "Old event 1"
    assert loaded_contents[1].text == "New event 1"
    assert loaded_contents[2].text == "Old event 2"
    assert loaded_contents[2].cache_prompt is True
    assert loaded_contents[3].text == "New event 2"
    assert loaded_contents[3].cache_prompt is True
