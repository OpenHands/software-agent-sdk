"""Tests for Anthropic thinking blocks support in LLM and Message classes."""

from litellm.types.llms.openai import ChatCompletionThinkingBlock
from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse, Usage
from pydantic import SecretStr

from openhands.sdk import LLM, Message, MessageEvent, TextContent, ThinkingBlock


def create_mock_response_with_thinking(
    content: str = "Test response",
    thinking_content: str = "Let me think about this...",
    response_id: str = "test-id",
):
    """Helper function to create mock responses with thinking blocks."""
    # Create a thinking block
    thinking_block = ChatCompletionThinkingBlock(
        type="thinking",
        thinking=thinking_content,
    )

    # Create the message with thinking blocks
    message = LiteLLMMessage(
        content=content,
        role="assistant",
        thinking_blocks=[thinking_block],
    )

    return ModelResponse(
        id=response_id,
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=message,
            )
        ],
        created=1234567890,
        model="claude-sonnet-4-5",
        object="chat.completion",
        usage=Usage(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
    )


def test_thinking_block_model():
    """Test ThinkingBlock model creation and validation."""
    # Test basic thinking block
    block = ThinkingBlock(
        thinking="Complex reasoning process...",
        signature="signature_hash_123",
    )

    assert block.type == "thinking"
    assert block.thinking == "Complex reasoning process..."
    assert block.signature == "signature_hash_123"


def test_message_with_thinking_blocks():
    """Test Message with thinking blocks fields."""
    from openhands.sdk.llm.message import Message, TextContent, ThinkingBlock

    thinking_block = ThinkingBlock(
        thinking="Let me think about this step by step...",
        signature="sig123",
    )

    message = Message(
        role="assistant",
        content=[TextContent(text="The answer is 42.")],
        thinking_blocks=[thinking_block],
    )

    assert len(message.thinking_blocks) == 1
    assert isinstance(message.thinking_blocks[0], ThinkingBlock)
    assert (
        message.thinking_blocks[0].thinking == "Let me think about this step by step..."
    )
    assert message.thinking_blocks[0].signature == "sig123"


def test_message_without_thinking_blocks():
    """Test Message without thinking blocks (default behavior)."""
    message = Message(role="assistant", content=[TextContent(text="The answer is 42.")])

    assert message.thinking_blocks == []


def test_message_from_llm_chat_message_with_thinking():
    """Test Message.from_llm_chat_message with thinking blocks."""
    # Create a mock LiteLLM message with thinking blocks
    thinking_block = ChatCompletionThinkingBlock(
        type="thinking",
        thinking="Let me analyze this problem...",
        signature="hash_456",
    )

    litellm_message = LiteLLMMessage(
        role="assistant",
        content="The answer is 42.",
        thinking_blocks=[thinking_block],
    )

    message = Message.from_llm_chat_message(litellm_message)

    assert message.role == "assistant"
    assert len(message.content) == 1
    assert isinstance(message.content[0], TextContent)
    assert message.content[0].text == "The answer is 42."

    # Check thinking blocks
    assert len(message.thinking_blocks) == 1
    assert isinstance(message.thinking_blocks[0], ThinkingBlock)
    assert message.thinking_blocks[0].thinking == "Let me analyze this problem..."
    assert message.thinking_blocks[0].signature == "hash_456"


def test_message_from_llm_chat_message_without_thinking():
    """Test Message.from_llm_chat_message without thinking blocks."""
    litellm_message = LiteLLMMessage(role="assistant", content="The answer is 42.")

    message = Message.from_llm_chat_message(litellm_message)

    assert message.role == "assistant"
    assert len(message.content) == 1
    assert isinstance(message.content[0], TextContent)
    assert message.content[0].text == "The answer is 42."

    assert message.thinking_blocks == []


def test_message_serialization_with_thinking_blocks():
    """Test Message serialization includes thinking blocks."""
    thinking_block = ThinkingBlock(
        thinking="Reasoning process...",
        signature="sig789",
    )

    message = Message(
        role="assistant",
        content=[TextContent(text="Answer")],
        thinking_blocks=[thinking_block],
    )

    serialized = message.model_dump()

    assert len(serialized["thinking_blocks"]) == 1
    assert serialized["thinking_blocks"][0]["thinking"] == "Reasoning process..."
    assert serialized["thinking_blocks"][0]["signature"] == "sig789"
    assert serialized["thinking_blocks"][0]["type"] == "thinking"


def test_message_serialization_without_thinking_blocks():
    """Test Message serialization without thinking blocks."""
    message = Message(role="assistant", content=[TextContent(text="Answer")])

    serialized = message.model_dump()

    assert serialized["thinking_blocks"] == []


def test_message_list_serializer_with_thinking_blocks():
    """Test Message._list_serializer includes thinking blocks as separate field."""
    thinking_block = ThinkingBlock(
        thinking="Let me think...",
        signature="sig_abc",
    )

    message = Message(
        role="assistant",
        content=[TextContent(text="The answer is 42.")],
        thinking_blocks=[thinking_block],
    )

    serialized = message._list_serializer()

    # Thinking blocks should be in a separate field, not in content
    assert "thinking_blocks" in serialized
    assert len(serialized["thinking_blocks"]) == 1
    assert serialized["thinking_blocks"][0]["type"] == "thinking"
    assert serialized["thinking_blocks"][0]["thinking"] == "Let me think..."
    assert serialized["thinking_blocks"][0]["signature"] == "sig_abc"

    # Content should only have text content
    content_list = serialized["content"]
    assert len(content_list) == 1
    assert content_list[0]["type"] == "text"
    assert content_list[0]["text"] == "The answer is 42."


def test_message_event_thinking_blocks_property():
    """Test MessageEvent thinking_blocks property."""
    thinking_block = ThinkingBlock(
        thinking="Complex reasoning...",
        signature="sig_def",
    )

    message = Message(
        role="assistant",
        content=[TextContent(text="Result")],
        thinking_blocks=[thinking_block],
    )

    event = MessageEvent(llm_message=message, source="agent")

    # Test thinking_blocks property
    assert len(event.thinking_blocks) == 1
    thinking_block = event.thinking_blocks[0]
    assert isinstance(thinking_block, ThinkingBlock)
    assert thinking_block.thinking == "Complex reasoning..."
    assert thinking_block.signature == "sig_def"


def test_message_event_str_with_thinking_blocks():
    """Test MessageEvent.__str__ includes thinking blocks count."""
    thinking_blocks = [
        ThinkingBlock(thinking="First thought", signature="sig1"),
        ThinkingBlock(thinking="Second thought", signature="sig2"),
    ]

    message = Message(
        role="assistant",
        content=[TextContent(text="Answer")],
        thinking_blocks=thinking_blocks,
    )

    event = MessageEvent(llm_message=message, source="agent")

    str_repr = str(event)

    # Should include thinking blocks count
    assert "[Thinking blocks: 2]" in str_repr


def test_multiple_thinking_blocks():
    """Test handling multiple thinking blocks."""
    thinking_blocks = [
        ThinkingBlock(thinking="First reasoning step", signature="sig1"),
        ThinkingBlock(thinking="Second reasoning step", signature="sig2"),
    ]

    message = Message(
        role="assistant",
        content=[TextContent(text="Conclusion")],
        thinking_blocks=thinking_blocks,
    )

    assert len(message.thinking_blocks) == 2
    assert isinstance(message.thinking_blocks[0], ThinkingBlock)
    assert message.thinking_blocks[0].thinking == "First reasoning step"
    assert isinstance(message.thinking_blocks[1], ThinkingBlock)
    assert message.thinking_blocks[1].thinking == "Second reasoning step"
    assert message.thinking_blocks[1].signature is not None

    # Test serialization - thinking blocks should be in separate field
    serialized = message._list_serializer()

    # Verify thinking_blocks field
    assert "thinking_blocks" in serialized
    assert len(serialized["thinking_blocks"]) == 2
    assert all(item["type"] == "thinking" for item in serialized["thinking_blocks"])

    # Verify content only has text
    content_list = serialized["content"]
    assert len(content_list) == 1
    assert content_list[0]["type"] == "text"


def test_llm_preserves_existing_thinking_blocks():
    """Test that LLM preserves existing thinking blocks and doesn't add duplicates."""
    # Create LLM with Anthropic model and reasoning effort
    llm = LLM(
        usage_id="test",
        model="anthropic/claude-sonnet-4-5",
        reasoning_effort="high",
        api_key=SecretStr("test-key"),
    )

    # Create message with existing thinking block
    existing_thinking = ThinkingBlock(
        thinking="I already have a thinking block", signature="existing_sig"
    )

    messages = [
        Message(
            role="assistant",
            content=[TextContent(text="Response with existing thinking")],
            thinking_blocks=[existing_thinking],
        ),
    ]

    # Format messages for LLM
    formatted_messages = llm.format_messages_for_llm(messages)

    # Check that the existing thinking block is preserved in separate field
    assert "thinking_blocks" in formatted_messages[0]
    thinking_blocks = formatted_messages[0]["thinking_blocks"]

    assert len(thinking_blocks) == 1
    assert thinking_blocks[0]["thinking"] == "I already have a thinking block"
    assert thinking_blocks[0]["signature"] == "existing_sig"


def test_thinking_blocks_in_message_dict():
    """Test that thinking blocks are placed as a field in message_dict."""
    thinking_block = ThinkingBlock(
        thinking="Analyzing the problem...",
        signature="sig_xyz",
    )

    message = Message(
        role="assistant",
        content=[TextContent(text="Here's my answer.")],
        thinking_blocks=[thinking_block],
        function_calling_enabled=True,  # Force list serializer
    )

    # Test via _list_serializer
    message_dict = message._list_serializer()

    # Verify thinking_blocks is a top-level field in message_dict
    assert "thinking_blocks" in message_dict
    assert isinstance(message_dict["thinking_blocks"], list)
    assert len(message_dict["thinking_blocks"]) == 1

    # Verify structure of thinking block in message_dict
    thinking_dict = message_dict["thinking_blocks"][0]
    assert thinking_dict["type"] == "thinking"
    assert thinking_dict["thinking"] == "Analyzing the problem..."
    assert thinking_dict["signature"] == "sig_xyz"

    # Verify content is separate from thinking_blocks
    assert "content" in message_dict
    assert len(message_dict["content"]) == 1
    assert message_dict["content"][0]["type"] == "text"


def test_thinking_blocks_in_message_dict_via_to_chat_dict():
    """Test that thinking blocks are included when calling to_chat_dict."""
    thinking_block = ThinkingBlock(
        thinking="Step-by-step reasoning...",
        signature="sig_chat",
    )

    message = Message(
        role="assistant",
        content=[TextContent(text="Final result.")],
        thinking_blocks=[thinking_block],
        function_calling_enabled=True,
    )

    # Test via to_chat_dict which calls _list_serializer
    chat_dict = message.to_chat_dict()

    # Verify thinking_blocks field exists
    assert "thinking_blocks" in chat_dict
    assert len(chat_dict["thinking_blocks"]) == 1
    assert chat_dict["thinking_blocks"][0]["thinking"] == "Step-by-step reasoning..."
    assert chat_dict["thinking_blocks"][0]["signature"] == "sig_chat"


def test_no_thinking_blocks_field_when_empty():
    """Test that thinking_blocks field is not added when there are no blocks."""
    message = Message(
        role="assistant",
        content=[TextContent(text="Simple response.")],
        function_calling_enabled=True,
    )

    message_dict = message._list_serializer()

    # When there are no thinking blocks, the field should not be present
    assert "thinking_blocks" not in message_dict
    assert "content" in message_dict


def test_thinking_blocks_only_for_assistant_role():
    """Test that thinking blocks are only added for assistant role messages."""
    thinking_block = ThinkingBlock(
        thinking="This should not appear...",
        signature="sig_user",
    )

    # Create a user message with thinking blocks (unusual but possible)
    user_message = Message(
        role="user",
        content=[TextContent(text="User input.")],
        thinking_blocks=[thinking_block],
        function_calling_enabled=True,
    )

    user_dict = user_message._list_serializer()

    # Thinking blocks should not be added for non-assistant roles
    assert "thinking_blocks" not in user_dict

    # Now test with assistant role
    assistant_message = Message(
        role="assistant",
        content=[TextContent(text="Assistant response.")],
        thinking_blocks=[thinking_block],
        function_calling_enabled=True,
    )

    assistant_dict = assistant_message._list_serializer()

    # Thinking blocks should be added for assistant role
    assert "thinking_blocks" in assistant_dict
    assert len(assistant_dict["thinking_blocks"]) == 1


def test_redacted_thinking_block_in_message_dict():
    """Test that redacted thinking blocks are also properly placed in message_dict."""
    from openhands.sdk.llm.message import RedactedThinkingBlock

    redacted_block = RedactedThinkingBlock(
        data="[REDACTED]",
    )

    message = Message(
        role="assistant",
        content=[TextContent(text="Response after redaction.")],
        thinking_blocks=[redacted_block],
        function_calling_enabled=True,
    )

    message_dict = message._list_serializer()

    # Verify redacted thinking block is in message_dict
    assert "thinking_blocks" in message_dict
    assert len(message_dict["thinking_blocks"]) == 1
    assert message_dict["thinking_blocks"][0]["type"] == "redacted_thinking"
    assert message_dict["thinking_blocks"][0]["data"] == "[REDACTED]"


def test_mixed_thinking_and_redacted_blocks():
    """Test handling of mixed thinking and redacted thinking blocks."""
    from openhands.sdk.llm.message import RedactedThinkingBlock

    thinking_block = ThinkingBlock(
        thinking="Active reasoning...",
        signature="sig_active",
    )
    redacted_block = RedactedThinkingBlock(data="[REDACTED]")

    message = Message(
        role="assistant",
        content=[TextContent(text="Mixed blocks response.")],
        thinking_blocks=[thinking_block, redacted_block],
        function_calling_enabled=True,
    )

    message_dict = message._list_serializer()

    # Verify both types are in message_dict
    assert "thinking_blocks" in message_dict
    assert len(message_dict["thinking_blocks"]) == 2
    assert message_dict["thinking_blocks"][0]["type"] == "thinking"
    assert message_dict["thinking_blocks"][1]["type"] == "redacted_thinking"
