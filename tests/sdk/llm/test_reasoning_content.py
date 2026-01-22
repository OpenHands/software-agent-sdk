"""Tests for reasoning content support in LLM and Message classes."""

from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse, Usage

from openhands.sdk.tool import Action


class _TestActionForReasoningContent(Action):
    """A test action used for testing reasoning content in ActionEvent.

    This class is defined at module level (rather than inside a test function) to
    ensure it's importable by Pydantic during serialization/deserialization.
    Defining it inside a test function causes test pollution when running tests
    in parallel with pytest-xdist.
    """

    action: str = "test"


def create_mock_response(content: str = "Test response", response_id: str = "test-id"):
    """Helper function to create properly structured mock responses."""
    return ModelResponse(
        id=response_id,
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=LiteLLMMessage(content=content, role="assistant"),
            )
        ],
        created=1234567890,
        model="claude-sonnet-4-20250514",
        object="chat.completion",
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def test_message_with_reasoning_content():
    """Test Message with reasoning content fields."""
    from openhands.sdk.llm.message import Message, TextContent

    message = Message(
        role="assistant",
        content=[TextContent(text="The answer is 42.")],
        reasoning_content="Let me think about this step by step...",
    )

    assert message.reasoning_content == "Let me think about this step by step..."


def test_message_without_reasoning_content():
    """Test Message without reasoning content (default behavior)."""
    from openhands.sdk.llm.message import Message, TextContent

    message = Message(role="assistant", content=[TextContent(text="The answer is 42.")])

    assert message.reasoning_content is None


def test_message_from_llm_chat_message_with_reasoning():
    """Test Message.from_llm_chat_message with reasoning content."""
    from openhands.sdk.llm.message import Message

    # Create a mock LiteLLM message with reasoning content
    litellm_message = LiteLLMMessage(role="assistant", content="The answer is 42.")
    # Add reasoning content as attributes
    litellm_message.reasoning_content = "Let me think about this..."

    message = Message.from_llm_chat_message(litellm_message)

    assert message.role == "assistant"
    assert len(message.content) == 1
    from openhands.sdk.llm.message import TextContent

    assert isinstance(message.content[0], TextContent)
    assert message.content[0].text == "The answer is 42."
    assert message.reasoning_content == "Let me think about this..."


def test_message_from_llm_chat_message_without_reasoning():
    """Test Message.from_llm_chat_message without reasoning content."""
    from openhands.sdk.llm.message import Message

    litellm_message = LiteLLMMessage(role="assistant", content="The answer is 42.")

    message = Message.from_llm_chat_message(litellm_message)

    assert message.role == "assistant"
    assert len(message.content) == 1
    from openhands.sdk.llm.message import TextContent

    assert isinstance(message.content[0], TextContent)
    assert message.content[0].text == "The answer is 42."
    assert message.reasoning_content is None


def test_message_serialization_with_reasoning():
    """Test Message serialization includes reasoning content."""
    from openhands.sdk.llm.message import Message, TextContent

    message = Message(
        role="assistant",
        content=[TextContent(text="Answer")],
        reasoning_content="Thinking process...",
    )

    serialized = message.model_dump()

    assert serialized["reasoning_content"] == "Thinking process..."


def test_message_serialization_without_reasoning():
    """Test Message serialization without reasoning content."""
    from openhands.sdk.llm.message import Message, TextContent

    message = Message(role="assistant", content=[TextContent(text="Answer")])

    serialized = message.model_dump()

    assert serialized["reasoning_content"] is None


def test_action_event_with_reasoning_content():
    """Test ActionEvent with reasoning content fields."""
    from openhands.sdk.event.llm_convertible import ActionEvent
    from openhands.sdk.llm.message import (
        MessageToolCall,
        TextContent,
    )

    # Create a tool call
    tool_call = MessageToolCall(
        id="test-id",
        name="test_tool",
        arguments='{"arg": "value"}',
        origin="completion",
    )

    action_event = ActionEvent(
        thought=[TextContent(text="I need to test this")],
        action=_TestActionForReasoningContent(),
        tool_name="test_tool",
        tool_call_id="test-id",
        tool_call=tool_call,
        llm_response_id="response-123",
        reasoning_content="Let me think about this step by step...",
    )

    # Test that reasoning content is preserved
    assert action_event.reasoning_content == "Let me think about this step by step..."

    # Test that reasoning content is included in the LLM message
    llm_message = action_event.to_llm_message()
    assert llm_message.reasoning_content == "Let me think about this step by step..."


def test_deepseek_reasoning_content_always_included():
    """Test that reasoning_content is always included for DeepSeek models.

    DeepSeek's thinking mode requires reasoning_content to be present in
    assistant messages during tool call turns, even if it's None.
    See: https://api-docs.deepseek.com/guides/thinking_mode#tool-calls
    """
    from openhands.sdk.llm.message import Message, MessageToolCall, TextContent

    # Create a tool call
    tool_call = MessageToolCall(
        id="call_123",
        name="get_date",
        arguments="{}",
        origin="completion",
    )

    # Test with reasoning_content = None
    message_with_none = Message(
        role="assistant",
        content=[TextContent(text="")],
        tool_calls=[tool_call],
        reasoning_content=None,
        send_reasoning_content=True,  # DeepSeek model feature
    )

    result = message_with_none.to_chat_dict()
    # reasoning_content should be present even when None
    assert "reasoning_content" in result
    assert result["reasoning_content"] is None

    # Test with reasoning_content = actual content
    message_with_content = Message(
        role="assistant",
        content=[TextContent(text="")],
        tool_calls=[tool_call],
        reasoning_content="Let me think about this...",
        send_reasoning_content=True,
    )

    result = message_with_content.to_chat_dict()
    assert "reasoning_content" in result
    assert result["reasoning_content"] == "Let me think about this..."


def test_deepseek_model_features():
    """Test that DeepSeek reasoner models have send_reasoning_content enabled."""
    from openhands.sdk.llm.utils.model_features import get_features

    # Test various DeepSeek model name formats
    test_cases = [
        "deepseek/deepseek-reasoner",
        "deepseek-reasoner",
        "litellm_proxy/deepseek/deepseek-reasoner",
        "DeepSeek/deepseek-reasoner",  # Case insensitive
    ]

    for model in test_cases:
        features = get_features(model)
        assert features.send_reasoning_content is True, (
            f"Expected send_reasoning_content=True for {model}"
        )

    # Test that non-reasoner DeepSeek models don't have this feature
    non_reasoner_models = [
        "deepseek/deepseek-chat",
        "deepseek-chat",
    ]

    for model in non_reasoner_models:
        features = get_features(model)
        assert features.send_reasoning_content is False, (
            f"Expected send_reasoning_content=False for {model}"
        )
