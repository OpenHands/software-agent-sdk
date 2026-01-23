"""Comprehensive tests for Message serialization behavior.

This module tests the Message class serialization, which now has two distinct paths:
1. Standard Pydantic serialization (model_dump/model_dump_json) for storage - always
   preserves structure
2. LLM API serialization (to_chat_dict) for provider consumption - adapts format
   based on capabilities

The refactored design separates storage concerns from API formatting concerns.
Tests are organized by serialization strategy to ensure clear separation of concerns.
"""

import json

from openhands.sdk.llm.message import (
    ImageContent,
    Message,
    TextContent,
)
from openhands.sdk.llm.serialization_context import LLMSerializationContext


DEFAULT_CTX = LLMSerializationContext(
    cache_enabled=False,
    vision_enabled=False,
    function_calling_enabled=False,
    force_string_serializer=False,
    send_reasoning_content=False,
)


class TestStorageSerialization:
    """Test storage serialization (model_dump/model_dump_json) - always preserves
    structure.
    """

    def test_basic_text_message_storage_serialization(self):
        """Test basic text message storage serialization preserves list structure."""
        message = Message(
            role="user",
            content=[TextContent(text="Hello, world!")],
        )

        # Storage serialization - always preserves structure
        storage_data = message.model_dump()
        assert isinstance(storage_data["content"], list)
        assert len(storage_data["content"]) == 1
        assert storage_data["content"][0]["text"] == "Hello, world!"
        assert storage_data["content"][0]["type"] == "text"
        assert storage_data["role"] == "user"

        # Round-trip storage works perfectly
        json_data = message.model_dump_json()
        deserialized = Message.model_validate_json(json_data)
        assert deserialized == message

    def test_vision_message_storage_serialization(self):
        """Test vision message storage serialization preserves all content types."""
        message = Message(
            role="user",
            content=[
                TextContent(text="What's in this image?"),
                ImageContent(
                    image_urls=["https://example.com/image.jpg"],
                ),
            ],
        )

        # Storage serialization - always list format
        storage_data = message.model_dump()
        assert isinstance(storage_data["content"], list)
        assert len(storage_data["content"]) == 2
        assert storage_data["content"][0]["type"] == "text"
        assert storage_data["content"][1]["type"] == "image"

        # Round-trip works
        deserialized = Message.model_validate(storage_data)
        assert deserialized == message

    def test_tool_response_message_storage_serialization(self):
        """Test tool response message storage serialization preserves all fields."""
        message = Message(
            role="tool",
            content=[TextContent(text="Weather in NYC: 72°F, sunny")],
            tool_call_id="call_123",
            name="get_weather",
        )

        # Storage serialization
        storage_data = message.model_dump()
        assert isinstance(storage_data["content"], list)
        assert storage_data["tool_call_id"] == "call_123"
        assert storage_data["name"] == "get_weather"

        # Round-trip works
        deserialized = Message.model_validate(storage_data)
        assert deserialized == message

    def test_empty_content_storage_serialization(self):
        """Test empty content list storage serialization."""
        message = Message(role="user", content=[])

        # Storage serialization
        storage_data = message.model_dump()
        assert storage_data["content"] == []

        # Round-trip works
        deserialized = Message.model_validate(storage_data)
        assert deserialized == message

    def test_field_defaults_after_minimal_deserialization(self):
        """Test field defaults are correct after deserializing minimal JSON."""
        minimal_json = json.dumps(
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]}
        )

        message = Message.model_validate_json(minimal_json)
        assert message.tool_calls is None
        assert message.tool_call_id is None
        assert message.name is None

        # Storage round-trip preserves defaults
        storage_data = message.model_dump()
        deserialized = Message.model_validate(storage_data)
        assert deserialized == message


class TestLLMAPISerialization:
    """Test LLM API serialization (to_chat_dict) - adapts format based on
    capabilities.
    """

    def test_basic_text_message_llm_string_serialization(self):
        """Test basic text message uses string format for LLM API."""
        message = Message(
            role="user",
            content=[TextContent(text="Hello, world!")],
        )

        # LLM API serialization - uses string format for simple messages
        llm_data = message.to_chat_dict(ctx=DEFAULT_CTX)
        assert isinstance(llm_data["content"], str)
        assert llm_data["content"] == "Hello, world!"
        assert llm_data["role"] == "user"

    def test_cache_enabled_triggers_list_serialization(self):
        """Test message with cache_enabled=True triggers list serializer for LLM."""
        message = Message(
            role="user",
            content=[TextContent(text="Hello, world!")],
        )

        # LLM API serialization - uses list format due to cache_enabled
        llm_data = message.to_chat_dict(
            ctx=LLMSerializationContext(
                **{**DEFAULT_CTX.as_dict(), "cache_enabled": True}
            )
        )
        assert isinstance(llm_data["content"], list)
        assert len(llm_data["content"]) == 1
        assert llm_data["content"][0]["text"] == "Hello, world!"

    def test_vision_enabled_triggers_list_serialization(self):
        """Test message with vision_enabled=True triggers list serializer for LLM."""
        message = Message(
            role="user",
            content=[
                TextContent(text="What's in this image?"),
                ImageContent(
                    image_urls=["https://example.com/image.jpg"],
                ),
            ],
        )

        # LLM API serialization - uses list format due to vision_enabled
        llm_data = message.to_chat_dict(
            ctx=LLMSerializationContext(
                **{**DEFAULT_CTX.as_dict(), "vision_enabled": True}
            )
        )
        assert isinstance(llm_data["content"], list)
        assert len(llm_data["content"]) == 2
        assert llm_data["content"][0]["text"] == "What's in this image?"
        assert llm_data["content"][1]["type"] == "image_url"

    def test_function_calling_enabled_triggers_list_serialization(self):
        """Test message with function_calling_enabled=True triggers list serializer for
        LLM.
        """
        message = Message(
            role="user",
            content=[TextContent(text="Call a function")],
        )

        # LLM API serialization - uses list format due to function_calling_enabled
        llm_data = message.to_chat_dict(
            ctx=LLMSerializationContext(
                **{**DEFAULT_CTX.as_dict(), "function_calling_enabled": True}
            )
        )
        assert isinstance(llm_data["content"], list)

    def test_force_string_serializer_override(self):
        """Test force_string_serializer=True overrides other settings for LLM."""
        message = Message(
            role="user",
            content=[TextContent(text="Hello, world!")],
        )

        # LLM API serialization - forced to string format
        llm_data = message.to_chat_dict(
            ctx=LLMSerializationContext(
                **{
                    **DEFAULT_CTX.as_dict(),
                    "cache_enabled": True,  # Would normally trigger list serializer
                    "force_string_serializer": True,  # But this forces string
                }
            )
        )
        assert isinstance(llm_data["content"], str)
        assert llm_data["content"] == "Hello, world!"

    def test_tool_response_message_llm_serialization(self):
        """Test tool response message uses string format for simple tool response."""
        message = Message(
            role="tool",
            content=[TextContent(text="Weather in NYC: 72°F, sunny")],
            tool_call_id="call_123",
            name="get_weather",
        )

        # LLM API serialization - uses string format for simple tool response
        llm_data = message.to_chat_dict(ctx=DEFAULT_CTX)
        assert isinstance(llm_data["content"], str)
        assert llm_data["content"] == "Weather in NYC: 72°F, sunny"
        assert llm_data["tool_call_id"] == "call_123"
        assert llm_data["name"] == "get_weather"

    def test_empty_content_llm_serialization(self):
        """Test empty content list converts to empty string in LLM serialization."""
        message = Message(
            role="user",
            content=[],
        )

        # LLM API serialization - string serializer converts empty list to empty string
        llm_data = message.to_chat_dict(ctx=DEFAULT_CTX)
        assert llm_data["content"] == ""

    def test_multiple_text_content_string_serialization(self):
        """Test multiple TextContent items are joined with newlines in LLM
        serialization.
        """
        message = Message(
            role="user",
            content=[
                TextContent(text="First line"),
                TextContent(text="Second line"),
                TextContent(text="Third line"),
            ],
        )

        # LLM API serialization - joins with newlines
        llm_data = message.to_chat_dict(ctx=DEFAULT_CTX)
        assert isinstance(llm_data["content"], str)
        assert llm_data["content"] == "First line\nSecond line\nThird line"

    def test_content_type_preservation_in_list_serializer(self):
        """Test content types are preserved correctly in list serializer for LLM."""
        message = Message(
            role="user",
            content=[
                TextContent(text="Describe this image"),
                ImageContent(
                    image_urls=["https://example.com/image.jpg"],
                ),
            ],
        )

        # LLM API serialization
        llm_data = message.to_chat_dict(
            ctx=LLMSerializationContext(
                **{**DEFAULT_CTX.as_dict(), "vision_enabled": True}
            )
        )
        assert isinstance(llm_data["content"], list)
        assert len(llm_data["content"]) == 2
        assert llm_data["content"][0]["type"] == "text"
        assert llm_data["content"][1]["type"] == "image_url"


class TestSerializationPathSelection:
    """Test the logic that determines which serialization path to use for LLM API."""

    def test_serialization_path_selection_logic(self):
        """Test the logic that determines which serialization path to use for LLM."""
        message = Message(
            role="user",
            content=[TextContent(text="test")],
        )

        # Default settings (all False) -> string serializer
        llm_data1 = message.to_chat_dict(ctx=DEFAULT_CTX)
        assert isinstance(llm_data1["content"], str)

        # cache_enabled -> list serializer
        llm_data2 = message.to_chat_dict(
            ctx=LLMSerializationContext(
                **{**DEFAULT_CTX.as_dict(), "cache_enabled": True}
            )
        )
        assert isinstance(llm_data2["content"], list)

        # vision_enabled -> list serializer
        llm_data3 = message.to_chat_dict(
            ctx=LLMSerializationContext(
                **{**DEFAULT_CTX.as_dict(), "vision_enabled": True}
            )
        )
        assert isinstance(llm_data3["content"], list)

        # function_calling_enabled -> list serializer
        llm_data4 = message.to_chat_dict(
            ctx=LLMSerializationContext(
                **{**DEFAULT_CTX.as_dict(), "function_calling_enabled": True}
            )
        )
        assert isinstance(llm_data4["content"], list)

        # force_string_serializer overrides everything
        llm_data5 = message.to_chat_dict(
            ctx=LLMSerializationContext(
                cache_enabled=True,
                vision_enabled=True,
                function_calling_enabled=True,
                force_string_serializer=True,
                send_reasoning_content=False,
            )
        )
        assert isinstance(llm_data5["content"], str)


class TestDualSerializationConsistency:
    """Test that both serialization strategies work together correctly."""

    def test_storage_always_list_llm_adapts(self):
        """Test that storage is always list format while LLM adapts based on
        settings.
        """
        messages = [
            Message(role="user", content=[TextContent(text="test1")]),
            Message(role="user", content=[TextContent(text="test2")]),
            Message(role="user", content=[TextContent(text="test3")]),
            Message(role="user", content=[TextContent(text="test4")]),
        ]

        serialization_configs = [
            # Default (all False) -> LLM uses string, storage uses list
            DEFAULT_CTX,
            # Cache enabled -> both use list
            LLMSerializationContext(**{**DEFAULT_CTX.as_dict(), "cache_enabled": True}),
            # Vision enabled -> both use list
            LLMSerializationContext(
                **{**DEFAULT_CTX.as_dict(), "vision_enabled": True}
            ),
            # Force string -> LLM uses string, storage uses list
            LLMSerializationContext(
                **{
                    **DEFAULT_CTX.as_dict(),
                    "cache_enabled": True,
                    "force_string_serializer": True,
                }
            ),
        ]

        for msg, ctx in zip(messages, serialization_configs):
            # Storage serialization is ALWAYS list format
            storage_data = msg.model_dump()
            assert isinstance(storage_data["content"], list)

            # LLM serialization adapts based on settings
            llm_data = msg.to_chat_dict(ctx=ctx)
            # Content type depends on the message settings
            assert "content" in llm_data

            # Round-trip storage always works
            deserialized = Message.model_validate(storage_data)
            assert deserialized == msg
