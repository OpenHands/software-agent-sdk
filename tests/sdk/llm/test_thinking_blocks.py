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
    """Test Message._list_serializer includes thinking blocks in content array."""
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
    content_list = serialized["content"]

    # Should have thinking block first, then text content
    assert len(content_list) == 2
    assert content_list[0]["type"] == "thinking"
    assert content_list[0]["thinking"] == "Let me think..."
    assert content_list[0]["signature"] == "sig_abc"
    assert content_list[1]["type"] == "text"
    assert content_list[1]["text"] == "The answer is 42."


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

    # Test serialization
    serialized = message._list_serializer()
    content_list = serialized["content"]
    assert len(content_list) == 3  # 2 thinking blocks + 1 text content
    assert all(item["type"] == "thinking" for item in content_list[:2])
    assert content_list[2]["type"] == "text"


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

    # Check that the existing thinking block is preserved and no duplicate is added
    content = formatted_messages[0]["content"]
    thinking_blocks = [item for item in content if item["type"] == "thinking"]

    assert len(thinking_blocks) == 1
    assert thinking_blocks[0]["thinking"] == "I already have a thinking block"
    assert thinking_blocks[0]["signature"] == "existing_sig"
