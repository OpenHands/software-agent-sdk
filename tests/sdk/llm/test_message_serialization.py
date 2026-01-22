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
        assert storage_data["cache_enabled"] is False

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
            vision_enabled=True,
        )

        # Storage serialization - always list format
        storage_data = message.model_dump()
        assert isinstance(storage_data["content"], list)
        assert len(storage_data["content"]) == 2
        assert storage_data["content"][0]["type"] == "text"
        assert storage_data["content"][1]["type"] == "image"
        assert storage_data["vision_enabled"] is True

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

    def test_all_boolean_fields_preserved_in_storage(self):
        """Test all boolean configuration fields are preserved in storage
        serialization.
        """
        message = Message(
            role="user",
            content=[TextContent(text="Test message")],
            cache_enabled=True,
            vision_enabled=True,
            function_calling_enabled=True,
            force_string_serializer=False,
        )

        # Storage serialization
        storage_data = message.model_dump()
        assert storage_data["cache_enabled"] is True
        assert storage_data["vision_enabled"] is True
        assert storage_data["function_calling_enabled"] is True
        assert storage_data["force_string_serializer"] is False

        # Round-trip works
        deserialized = Message.model_validate(storage_data)
        assert deserialized == message

    def test_field_defaults_after_minimal_deserialization(self):
        """Test field defaults are correct after deserializing minimal JSON."""
        minimal_json = json.dumps(
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]}
        )

        message = Message.model_validate_json(minimal_json)
        assert message.cache_enabled is False
        assert message.vision_enabled is False
        assert message.function_calling_enabled is False
        assert message.force_string_serializer is False
        assert message.tool_calls is None
        assert message.tool_call_id is None
        assert message.name is None

        # Storage round-trip preserves defaults
        storage_data = message.model_dump()
        deserialized = Message.model_validate(storage_data)
        assert deserialized == message

    def test_regression_cache_enabled_preservation(self):
        """Regression test: ensure cache_enabled field is preserved after
        serialization.
        """
        message = Message(
            role="user",
            content=[TextContent(text="Test")],
            cache_enabled=True,
        )

        # Storage round-trip
        storage_json = message.model_dump_json()
        storage_deserialized = Message.model_validate_json(storage_json)
        assert storage_deserialized.cache_enabled is True
        assert storage_deserialized == message


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
        llm_data = message.to_chat_dict()
        assert isinstance(llm_data["content"], str)
        assert llm_data["content"] == "Hello, world!"
        assert llm_data["role"] == "user"

    def test_cache_enabled_triggers_list_serialization(self):
        """Test message with cache_enabled=True triggers list serializer for LLM."""
        message = Message(
            role="user",
            content=[TextContent(text="Hello, world!")],
            cache_enabled=True,
        )

        # LLM API serialization - uses list format due to cache_enabled
        llm_data = message.to_chat_dict()
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

        # LLM API serialization - uses list format due to vision_enabled parameter
        llm_data = message.to_chat_dict(vision_enabled=True)
        assert isinstance(llm_data["content"], list)
        assert len(llm_data["content"]) == 2
        assert llm_data["content"][0]["text"] == "What's in this image?"
        assert llm_data["content"][1]["type"] == "image_url"

    def test_vision_enabled_false_excludes_images(self):
        """Test that vision_enabled=False excludes images from serialization."""
        message = Message(
            role="user",
            content=[
                TextContent(text="What's in this image?"),
                ImageContent(
                    image_urls=["https://example.com/image.jpg"],
                ),
            ],
        )

        # With vision_enabled=False, images should be excluded
        llm_data = message.to_chat_dict(vision_enabled=False)
        # Should use string serializer since vision is disabled
        assert isinstance(llm_data["content"], str)
        assert llm_data["content"] == "What's in this image?"

    def test_deprecated_vision_enabled_field_backward_compat(self):
        """Test backward compatibility: deprecated field still works.

        Note: The deprecation warning will only be emitted when the SDK version
        reaches 1.10.0 (the deprecated_in version). Until then, the field works
        silently for backward compatibility.
        """
        message = Message(
            role="user",
            content=[
                TextContent(text="What's in this image?"),
                ImageContent(
                    image_urls=["https://example.com/image.jpg"],
                ),
            ],
            vision_enabled=True,  # Deprecated way to enable vision
        )

        # Calling to_chat_dict() without parameter should use deprecated field
        llm_data = message.to_chat_dict()

        # Should still work - images included via deprecated field
        assert isinstance(llm_data["content"], list)
        assert len(llm_data["content"]) == 2
        assert llm_data["content"][0]["type"] == "text"
        assert llm_data["content"][1]["type"] == "image_url"

    def test_deprecated_vision_enabled_field_emits_warning_at_deprecation_version(
        self,
    ):
        """Test that deprecation warning is emitted at the right version."""
        import warnings

        from openhands.sdk.utils.deprecation import warn_deprecated

        # Simulate being at version 1.10.0 when the deprecation is active
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            warn_deprecated(
                "Message.vision_enabled field",
                deprecated_in="1.10.0",
                removed_in="1.11.0",
                current_version="1.10.0",  # Override to simulate future version
                details="Pass vision_enabled as a parameter to to_chat_dict() instead.",
            )

            # Should have emitted deprecation warning
            assert len(w) == 1
            assert "deprecated" in str(w[0].message).lower()
            assert "1.10.0" in str(w[0].message)

    def test_function_calling_enabled_triggers_list_serialization(self):
        """Test message with function_calling_enabled=True triggers list serializer for
        LLM.
        """
        message = Message(
            role="user",
            content=[TextContent(text="Call a function")],
            function_calling_enabled=True,
        )

        # LLM API serialization - uses list format due to function_calling_enabled
        llm_data = message.to_chat_dict()
        assert isinstance(llm_data["content"], list)

    def test_force_string_serializer_override(self):
        """Test force_string_serializer=True overrides other settings for LLM."""
        message = Message(
            role="user",
            content=[TextContent(text="Hello, world!")],
            cache_enabled=True,  # Would normally trigger list serializer
            force_string_serializer=True,  # But this forces string
        )

        # LLM API serialization - forced to string format
        llm_data = message.to_chat_dict()
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
        llm_data = message.to_chat_dict()
        assert isinstance(llm_data["content"], str)
        assert llm_data["content"] == "Weather in NYC: 72°F, sunny"
        assert llm_data["tool_call_id"] == "call_123"
        assert llm_data["name"] == "get_weather"

    def test_empty_content_llm_serialization(self):
        """Test empty content list converts to empty string in LLM serialization."""
        message = Message(role="user", content=[])

        # LLM API serialization - string serializer converts empty list to empty string
        llm_data = message.to_chat_dict()
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
        llm_data = message.to_chat_dict()
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

        # LLM API serialization - pass vision_enabled as parameter
        llm_data = message.to_chat_dict(vision_enabled=True)
        assert isinstance(llm_data["content"], list)
        assert len(llm_data["content"]) == 2
        assert llm_data["content"][0]["type"] == "text"
        assert llm_data["content"][1]["type"] == "image_url"


class TestSerializationPathSelection:
    """Test the logic that determines which serialization path to use for LLM API."""

    def test_serialization_path_selection_logic(self):
        """Test the logic that determines which serialization path to use for LLM."""
        # Default settings -> string serializer
        message1 = Message(role="user", content=[TextContent(text="test")])
        llm_data1 = message1.to_chat_dict(vision_enabled=False)
        assert isinstance(llm_data1["content"], str)

        # cache_enabled -> list serializer
        message2 = Message(
            role="user", content=[TextContent(text="test")], cache_enabled=True
        )
        llm_data2 = message2.to_chat_dict(vision_enabled=False)
        assert isinstance(llm_data2["content"], list)

        # vision_enabled parameter -> list serializer
        message3 = Message(role="user", content=[TextContent(text="test")])
        llm_data3 = message3.to_chat_dict(vision_enabled=True)
        assert isinstance(llm_data3["content"], list)

        # function_calling_enabled -> list serializer
        message4 = Message(
            role="user",
            content=[TextContent(text="test")],
            function_calling_enabled=True,
        )
        llm_data4 = message4.to_chat_dict(vision_enabled=False)
        assert isinstance(llm_data4["content"], list)

        # force_string_serializer overrides everything
        message5 = Message(
            role="user",
            content=[TextContent(text="test")],
            cache_enabled=True,
            function_calling_enabled=True,
            force_string_serializer=True,
        )
        llm_data5 = message5.to_chat_dict(vision_enabled=True)
        assert isinstance(llm_data5["content"], str)


class TestDualSerializationConsistency:
    """Test that both serialization strategies work together correctly."""

    def test_storage_always_list_llm_adapts(self):
        """Test that storage is always list format while LLM adapts based on
        settings.
        """
        # Test cases: (message, vision_enabled_param, expected_llm_content_type)
        test_cases = [
            # Default -> LLM uses string, storage uses list
            (Message(role="user", content=[TextContent(text="test1")]), False, str),
            # Cache enabled -> both use list
            (
                Message(
                    role="user", content=[TextContent(text="test2")], cache_enabled=True
                ),
                False,
                list,
            ),
            # Vision enabled via parameter -> both use list
            (Message(role="user", content=[TextContent(text="test3")]), True, list),
            # Force string -> LLM uses string, storage uses list
            (
                Message(
                    role="user",
                    content=[TextContent(text="test4")],
                    cache_enabled=True,
                    force_string_serializer=True,
                ),
                True,
                str,
            ),
        ]

        for msg, vision_param, expected_type in test_cases:
            # Storage serialization is ALWAYS list format
            storage_data = msg.model_dump()
            assert isinstance(storage_data["content"], list)

            # LLM serialization adapts based on settings
            llm_data = msg.to_chat_dict(vision_enabled=vision_param)
            # Content type depends on the message settings
            assert "content" in llm_data
            assert isinstance(llm_data["content"], expected_type)

            # Round-trip storage always works
            deserialized = Message.model_validate(storage_data)
            assert deserialized == msg
