"""Backward compatibility tests for Message and TextContent serialization.

These tests verify that events serialized in previous SDK versions can still
be loaded correctly. This is critical for production systems that may resume
conversations created with older SDK versions.

IMPORTANT: These tests should NOT be modified to fix unit test failures.
If a test fails, it indicates that the code should be updated to accommodate
the old serialization format, NOT that the test should be changed.

VERSION NAMING CONVENTION: The version in the test name should be the LAST
version where a particular event structure exists. For example, if a field
was removed in v1.11.1, the test should be named for v1.10.x (the last version
with that field).
"""

import json
import warnings

from openhands.sdk.llm.message import Message, TextContent


# =============================================================================
# TextContent Backward Compatibility Tests
# =============================================================================


def test_v1_10_0_text_content_with_enable_truncation():
    """Verify TextContent with enable_truncation loads (last version: v1.10.0).

    enable_truncation was added in v1.6.0 and removed in v1.11.1.
    v1.10.0 was the LAST version with this field.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
    old_format = {
        "type": "text",
        "text": "Tool execution result: command completed successfully",
        "cache_prompt": False,
        "enable_truncation": True,
    }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        content = TextContent.model_validate(old_format)

    assert content.text == "Tool execution result: command completed successfully"
    assert content.type == "text"
    assert content.cache_prompt is False


def test_v1_10_0_text_content_with_enable_truncation_false():
    """Verify TextContent with enable_truncation=false loads (last version: v1.10.0).

    Some use cases explicitly set enable_truncation=false to preserve full content.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
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


def test_text_content_current_format():
    """Verify TextContent in current format loads (v1.11.1+).

    Current format without enable_truncation field.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
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
    """Verify Message with deprecated serialization fields loads (last version: v1.9.0).

    In v1.9.0, Message had cache_enabled, vision_enabled, function_calling_enabled,
    force_string_serializer, and send_reasoning_content as instance fields.
    These were removed in v1.9.1+. v1.9.0 was the LAST version with these fields.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
    old_format = {
        "role": "assistant",
        "content": [
            {
                "type": "text",
                "text": "I'll help you with that.",
                "cache_prompt": False,
                "enable_truncation": True,
            }
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


def test_message_current_format():
    """Verify Message in current format loads (v1.9.1+).

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
# Mixed Version Conversation Test
# =============================================================================


def test_mixed_version_conversation_loads():
    """Verify a conversation with events from multiple SDK versions loads.

    Real conversations may have events serialized with different SDK versions
    if the SDK was upgraded mid-conversation or if resuming an old conversation.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
    events = [
        # Old format with deprecated fields
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
            "send_reasoning_content": False,
            "tool_calls": None,
            "tool_call_id": None,
            "name": None,
        },
        # Current format without deprecated fields
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Hi there!", "cache_prompt": False}],
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

    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content[0].text == "Hello"  # type: ignore[union-attr]
    assert messages[1].role == "assistant"
    assert messages[1].content[0].text == "Hi there!"  # type: ignore[union-attr]


# =============================================================================
# JSON Deserialization Tests
# =============================================================================


def test_v1_10_0_text_content_json_deserialization():
    """Test JSON string deserialization for TextContent with deprecated fields.

    Uses model_validate_json to ensure JSON string parsing works.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
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
    """Test JSON string deserialization for Message with deprecated fields.

    Uses model_validate_json to ensure JSON string parsing works.

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
