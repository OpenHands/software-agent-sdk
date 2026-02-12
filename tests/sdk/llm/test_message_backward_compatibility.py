"""Backward compatibility tests for Message and TextContent serialization.

These tests verify that events serialized in previous SDK versions can still
be loaded correctly. This is critical for production systems that may resume
conversations created with older SDK versions.

IMPORTANT: These tests should NOT be modified to fix unit test failures.
If a test fails, it indicates that the code should be updated to accommodate
the old serialization format, NOT that the test should be changed.

Each test includes the SDK version where the serialization format was used
and the exact JSON structure that was persisted.
"""

import json
import warnings

from openhands.sdk.llm.message import ImageContent, Message, TextContent


# =============================================================================
# TextContent Backward Compatibility Tests
# =============================================================================


def test_v1_8_0_text_content_with_enable_truncation():
    """Verify TextContent serialized in v1.8.0 with enable_truncation loads.

    In v1.8.0, TextContent had an enable_truncation field that controlled
    whether text would be truncated. This field was removed in v1.11.1 but
    old events may still contain it.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
    # Exact JSON structure from v1.8.0 TextContent serialization
    old_format = {
        "type": "text",
        "text": "Tool execution result: command completed successfully",
        "cache_prompt": False,
        "enable_truncation": True,
    }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # Suppress deprecation warnings
        content = TextContent.model_validate(old_format)

    assert content.text == "Tool execution result: command completed successfully"
    assert content.type == "text"
    assert content.cache_prompt is False


def test_v1_9_0_text_content_with_enable_truncation_false():
    """Verify TextContent serialized in v1.9.0 with enable_truncation=false loads.

    Some use cases (like LLM judge in behavior tests) explicitly set
    enable_truncation=false to preserve full content.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
    # Exact JSON structure from v1.9.0 where enable_truncation was explicitly false
    old_format = {
        "type": "text",
        "text": "This is a very long response that should not be truncated",
        "cache_prompt": False,
        "enable_truncation": False,
    }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        content = TextContent.model_validate(old_format)

    assert content.text == "This is a very long response that should not be truncated"
    assert content.type == "text"


def test_v1_10_0_text_content_with_enable_truncation():
    """Verify TextContent serialized in v1.10.0 with enable_truncation loads.

    v1.10.0 was the last version before enable_truncation was removed.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
    old_format = {
        "type": "text",
        "text": "Agent response content",
        "cache_prompt": True,
        "enable_truncation": True,
    }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        content = TextContent.model_validate(old_format)

    assert content.text == "Agent response content"
    assert content.cache_prompt is True


def test_v1_11_4_text_content_without_enable_truncation():
    """Verify TextContent serialized in v1.11.4 (current format) loads.

    v1.11.1+ removed the enable_truncation field. This is the current format.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
    # Current format without enable_truncation
    current_format = {
        "type": "text",
        "text": "Current SDK format",
        "cache_prompt": False,
    }

    content = TextContent.model_validate(current_format)

    assert content.text == "Current SDK format"
    assert content.cache_prompt is False


# =============================================================================
# Message Backward Compatibility Tests
# =============================================================================


def test_v1_9_0_message_with_deprecated_fields():
    """Verify Message serialized in v1.9.0 with serialization control fields loads.

    In v1.9.0, Message had cache_enabled, vision_enabled, function_calling_enabled,
    force_string_serializer, and send_reasoning_content as instance fields.
    These were removed in v1.9.1+ but old events may still contain them.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
    # Exact structure from v1.9.0 Message serialization
    old_format = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "I'll help you with that.", "cache_prompt": False}
        ],
        "cache_enabled": True,
        "vision_enabled": False,
        "function_calling_enabled": True,
        "force_string_serializer": False,
        "send_reasoning_content": False,
        "tool_calls": None,
        "tool_call_id": None,
        "name": None,
        "reasoning_content": None,
        "thinking_blocks": [],
        "responses_reasoning_item": None,
    }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        message = Message.model_validate(old_format)

    assert message.role == "assistant"
    assert len(message.content) == 1
    content = message.content[0]
    assert isinstance(content, TextContent)
    assert content.text == "I'll help you with that."


def test_v1_8_0_message_with_all_deprecated_fields():
    """Verify Message serialized in v1.8.0 with all deprecated fields loads.

    v1.8.0 Message had serialization control fields but without
    send_reasoning_content (added later).

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
    old_format = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "Hello",
                "cache_prompt": False,
                "enable_truncation": True,
            }
        ],
        "cache_enabled": False,
        "vision_enabled": True,
        "function_calling_enabled": False,
        "force_string_serializer": True,
        "tool_calls": None,
        "tool_call_id": None,
        "name": None,
    }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        message = Message.model_validate(old_format)

    assert message.role == "user"
    assert len(message.content) == 1
    content = message.content[0]
    assert isinstance(content, TextContent)
    assert content.text == "Hello"


def test_v1_9_0_message_tool_role():
    """Verify Message with tool role from v1.9.0 loads.

    Tool messages have additional fields like tool_call_id and name.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
    old_format = {
        "role": "tool",
        "content": [
            {
                "type": "text",
                "text": "Command executed successfully",
                "cache_prompt": False,
                "enable_truncation": True,
            }
        ],
        "cache_enabled": False,
        "vision_enabled": False,
        "function_calling_enabled": False,
        "force_string_serializer": False,
        "send_reasoning_content": False,
        "tool_calls": None,
        "tool_call_id": "call_abc123",
        "name": "terminal",
        "reasoning_content": None,
        "thinking_blocks": [],
        "responses_reasoning_item": None,
    }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        message = Message.model_validate(old_format)

    assert message.role == "tool"
    assert message.tool_call_id == "call_abc123"
    assert message.name == "terminal"


def test_v1_9_0_message_with_reasoning_content():
    """Verify Message with reasoning_content from v1.9.0 loads.

    Messages from reasoning models (o1, Claude thinking, DeepSeek R1) include
    reasoning_content.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
    old_format = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "The answer is 42.", "cache_prompt": False}
        ],
        "cache_enabled": False,
        "vision_enabled": False,
        "function_calling_enabled": False,
        "force_string_serializer": False,
        "send_reasoning_content": True,
        "tool_calls": None,
        "tool_call_id": None,
        "name": None,
        "reasoning_content": "Let me think through this step by step...",
        "thinking_blocks": [],
        "responses_reasoning_item": None,
    }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        message = Message.model_validate(old_format)

    assert message.role == "assistant"
    assert message.reasoning_content == "Let me think through this step by step..."


def test_v1_11_4_message_current_format():
    """Verify Message in current v1.11.4 format loads.

    Current format without deprecated serialization control fields.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
    current_format = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Current format message", "cache_prompt": False}
        ],
        "tool_calls": None,
        "tool_call_id": None,
        "name": None,
        "reasoning_content": None,
        "thinking_blocks": [],
        "responses_reasoning_item": None,
    }

    message = Message.model_validate(current_format)

    assert message.role == "assistant"
    content = message.content[0]
    assert isinstance(content, TextContent)
    assert content.text == "Current format message"


# =============================================================================
# Mixed Version Conversation Tests
# =============================================================================


def test_mixed_version_conversation_loads():
    """Verify a conversation with events from multiple SDK versions loads.

    Real conversations may have events serialized with different SDK versions
    if the SDK was upgraded mid-conversation or if resuming an old conversation.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
    # Simulate a conversation with events from different versions
    events = [
        # v1.8.0 format user message with enable_truncation
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Hello",
                    "cache_prompt": False,
                    "enable_truncation": True,
                }
            ],
            "cache_enabled": False,
            "vision_enabled": False,
            "function_calling_enabled": False,
            "force_string_serializer": False,
            "tool_calls": None,
            "tool_call_id": None,
            "name": None,
        },
        # v1.9.0 format assistant message with send_reasoning_content
        {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "Hi there!",
                    "cache_prompt": False,
                    "enable_truncation": True,
                }
            ],
            "cache_enabled": False,
            "vision_enabled": False,
            "function_calling_enabled": True,
            "force_string_serializer": False,
            "send_reasoning_content": False,
            "tool_calls": None,
            "tool_call_id": None,
            "name": None,
            "reasoning_content": None,
            "thinking_blocks": [],
            "responses_reasoning_item": None,
        },
        # v1.11.4 format (current) without deprecated fields
        {
            "role": "user",
            "content": [{"type": "text", "text": "Thanks!", "cache_prompt": False}],
            "tool_calls": None,
            "tool_call_id": None,
            "name": None,
            "reasoning_content": None,
            "thinking_blocks": [],
            "responses_reasoning_item": None,
        },
    ]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        messages = [Message.model_validate(e) for e in events]

    assert len(messages) == 3
    assert messages[0].role == "user"
    content0 = messages[0].content[0]
    assert isinstance(content0, TextContent)
    assert content0.text == "Hello"

    assert messages[1].role == "assistant"
    content1 = messages[1].content[0]
    assert isinstance(content1, TextContent)
    assert content1.text == "Hi there!"

    assert messages[2].role == "user"
    content2 = messages[2].content[0]
    assert isinstance(content2, TextContent)
    assert content2.text == "Thanks!"


def test_v1_8_0_message_with_tool_calls():
    """Verify Message with tool_calls from v1.8.0 loads.

    Assistant messages with tool calls have a specific structure.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
    old_format = {
        "role": "assistant",
        "content": [
            {
                "type": "text",
                "text": "I'll run that command for you.",
                "cache_prompt": False,
                "enable_truncation": True,
            }
        ],
        "cache_enabled": True,
        "vision_enabled": False,
        "function_calling_enabled": True,
        "force_string_serializer": False,
        "tool_calls": [
            {
                "id": "call_xyz789",
                "name": "terminal",
                "arguments": '{"command": "ls -la"}',
                "origin": "completion",
            }
        ],
        "tool_call_id": None,
        "name": None,
    }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        message = Message.model_validate(old_format)

    assert message.role == "assistant"
    assert message.tool_calls is not None
    assert len(message.tool_calls) == 1
    assert message.tool_calls[0].name == "terminal"
    assert message.tool_calls[0].id == "call_xyz789"


def test_v1_9_0_message_with_thinking_blocks():
    """Verify Message with thinking_blocks from v1.9.0 loads.

    Messages from Claude with extended thinking have thinking blocks.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
    old_format = {
        "role": "assistant",
        "content": [
            {
                "type": "text",
                "text": "Based on my analysis...",
                "cache_prompt": False,
                "enable_truncation": True,
            }
        ],
        "cache_enabled": False,
        "vision_enabled": False,
        "function_calling_enabled": False,
        "force_string_serializer": False,
        "send_reasoning_content": False,
        "tool_calls": None,
        "tool_call_id": None,
        "name": None,
        "reasoning_content": None,
        "thinking_blocks": [
            {
                "type": "thinking",
                "thinking": "Let me analyze this problem step by step...",
                "signature": "sig_abc123",
            }
        ],
        "responses_reasoning_item": None,
    }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        message = Message.model_validate(old_format)

    assert message.role == "assistant"
    assert len(message.thinking_blocks) == 1
    assert message.thinking_blocks[0].thinking == (  # type: ignore[union-attr]
        "Let me analyze this problem step by step..."
    )


def test_v1_9_0_message_with_image_content():
    """Verify Message with ImageContent from v1.9.0 loads.

    Messages can contain both text and image content.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
    # Base64 encoded 1x1 transparent PNG
    tiny_png = (
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfF"
        "cSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )

    old_format = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "What is in this image?",
                "cache_prompt": False,
                "enable_truncation": True,
            },
            {"type": "image", "image_urls": [tiny_png], "cache_prompt": False},
        ],
        "cache_enabled": False,
        "vision_enabled": True,
        "function_calling_enabled": False,
        "force_string_serializer": False,
        "send_reasoning_content": False,
        "tool_calls": None,
        "tool_call_id": None,
        "name": None,
        "reasoning_content": None,
        "thinking_blocks": [],
        "responses_reasoning_item": None,
    }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        message = Message.model_validate(old_format)

    assert message.role == "user"
    assert len(message.content) == 2

    text_content = message.content[0]
    assert isinstance(text_content, TextContent)
    assert text_content.text == "What is in this image?"

    image_content = message.content[1]
    assert isinstance(image_content, ImageContent)
    assert message.contains_image


def test_v1_8_0_json_deserialization():
    """Test that actual JSON string deserialization works for v1.8.0 format.

    This test uses model_validate_json to ensure JSON string parsing works.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
    # Simulating the exact JSON that would be stored in the event store
    serialized_json = json.dumps(
        {
            "type": "text",
            "text": "JSON deserialization test",
            "cache_prompt": False,
            "enable_truncation": True,
        }
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        content = TextContent.model_validate_json(serialized_json)

    assert content.text == "JSON deserialization test"


def test_v1_9_0_message_json_deserialization():
    """Test that actual JSON string deserialization works for Message.

    This test uses model_validate_json to ensure JSON string parsing works.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
    serialized_json = json.dumps(
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "JSON test",
                    "cache_prompt": False,
                    "enable_truncation": True,
                }
            ],
            "cache_enabled": False,
            "vision_enabled": False,
            "function_calling_enabled": False,
            "force_string_serializer": False,
            "send_reasoning_content": False,
            "tool_calls": None,
            "tool_call_id": None,
            "name": None,
        }
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        message = Message.model_validate_json(serialized_json)

    assert message.role == "user"
    content = message.content[0]
    assert isinstance(content, TextContent)
    assert content.text == "JSON test"
