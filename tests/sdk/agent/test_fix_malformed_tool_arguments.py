"""Tests for fix_malformed_tool_arguments helper function.

This module tests the fix_malformed_tool_arguments helper that automatically
decodes JSON strings for list/dict fields. This handles cases where LLMs
(like GLM-4) return array/object values as JSON strings instead of native
JSON arrays/objects.
"""

from typing import Annotated

import pytest
from pydantic import Field, ValidationError

from openhands.sdk.agent.utils import fix_malformed_tool_arguments
from openhands.sdk.tool.schema import Action


class JsonDecodingTestAction(Action):
    """Test action with list and dict fields."""

    items: list[str] = Field(description="A list of items")
    config: dict[str, int] = Field(description="Configuration dictionary")
    name: str = Field(description="A regular string field")


class JsonDecodingAnnotatedAction(Action):
    """Test action with Annotated types."""

    items: Annotated[list[str], Field(description="A list of items")]
    config: Annotated[dict[str, int], Field(description="Configuration dictionary")]


class JsonDecodingAliasAction(Action):
    """Test action with field aliases."""

    my_list: list[int] = Field(alias="myList", description="A list with alias")
    my_dict: dict[str, str] = Field(alias="myDict", description="A dict with alias")


class JsonDecodingOptionalAction(Action):
    """Test action with optional list/dict fields."""

    items: list[str] | None = Field(default=None, description="Optional list")
    config: dict[str, int] | None = Field(default=None, description="Optional dict")


def test_decode_json_string_list():
    """Test that JSON string lists are decoded to native lists."""
    data = {
        "items": '["a", "b", "c"]',
        "config": '{"x": 1, "y": 2}',
        "name": "test",
    }
    fixed_data = fix_malformed_tool_arguments(data, JsonDecodingTestAction)
    action = JsonDecodingTestAction.model_validate(fixed_data)

    assert action.items == ["a", "b", "c"]
    assert action.config == {"x": 1, "y": 2}
    assert action.name == "test"


def test_decode_json_string_dict():
    """Test that JSON string dicts are decoded to native dicts."""
    data = {
        "items": '["item1", "item2"]',
        "config": '{"key1": 10, "key2": 20}',
        "name": "dict_test",
    }
    fixed_data = fix_malformed_tool_arguments(data, JsonDecodingTestAction)
    action = JsonDecodingTestAction.model_validate(fixed_data)

    assert action.items == ["item1", "item2"]
    assert action.config == {"key1": 10, "key2": 20}
    assert action.name == "dict_test"


def test_native_list_dict_passthrough():
    """Test that native lists and dicts pass through unchanged."""
    data = {
        "items": ["direct", "list"],
        "config": {"direct": 42},
        "name": "native_test",
    }
    fixed_data = fix_malformed_tool_arguments(data, JsonDecodingTestAction)
    action = JsonDecodingTestAction.model_validate(fixed_data)

    assert action.items == ["direct", "list"]
    assert action.config == {"direct": 42}
    assert action.name == "native_test"


def test_regular_string_not_decoded():
    """Test that regular string fields are not affected by JSON decoding."""
    data = {
        "items": "[]",
        "config": "{}",
        "name": "this is not json but a regular string",
    }
    fixed_data = fix_malformed_tool_arguments(data, JsonDecodingTestAction)
    action = JsonDecodingTestAction.model_validate(fixed_data)

    assert action.items == []
    assert action.config == {}
    # Regular string field should NOT be decoded
    assert action.name == "this is not json but a regular string"


def test_annotated_types():
    """Test that Annotated types are properly handled."""
    data = {
        "items": '["x", "y", "z"]',
        "config": '{"a": 1, "b": 2}',
    }
    fixed_data = fix_malformed_tool_arguments(data, JsonDecodingAnnotatedAction)
    action = JsonDecodingAnnotatedAction.model_validate(fixed_data)

    assert action.items == ["x", "y", "z"]
    assert action.config == {"a": 1, "b": 2}


def test_field_aliases():
    """Test that field aliases are properly handled."""
    data = {
        "myList": "[1, 2, 3]",
        "myDict": '{"key": "value"}',
    }
    fixed_data = fix_malformed_tool_arguments(data, JsonDecodingAliasAction)
    action = JsonDecodingAliasAction.model_validate(fixed_data)

    assert action.my_list == [1, 2, 3]
    assert action.my_dict == {"key": "value"}


def test_optional_fields_with_json_strings():
    """Test that optional list/dict fields work with JSON strings."""
    data = {
        "items": '["opt1", "opt2"]',
        "config": '{"opt": 99}',
    }
    fixed_data = fix_malformed_tool_arguments(data, JsonDecodingOptionalAction)
    action = JsonDecodingOptionalAction.model_validate(fixed_data)

    assert action.items == ["opt1", "opt2"]
    assert action.config == {"opt": 99}


def test_optional_fields_with_none():
    """Test that optional fields can be None."""
    data = {}
    fixed_data = fix_malformed_tool_arguments(data, JsonDecodingOptionalAction)
    action = JsonDecodingOptionalAction.model_validate(fixed_data)

    assert action.items is None
    assert action.config is None


def test_optional_fields_with_native_values():
    """Test that optional fields work with native values."""
    data = {
        "items": ["native1", "native2"],
        "config": {"native": 100},
    }
    fixed_data = fix_malformed_tool_arguments(data, JsonDecodingOptionalAction)
    action = JsonDecodingOptionalAction.model_validate(fixed_data)

    assert action.items == ["native1", "native2"]
    assert action.config == {"native": 100}


def test_invalid_json_string_rejected():
    """Test that invalid JSON strings are rejected with validation error."""
    data = {
        "items": "not valid json",
        "config": "{}",
        "name": "test",
    }
    fixed_data = fix_malformed_tool_arguments(data, JsonDecodingTestAction)

    with pytest.raises(ValidationError) as exc_info:
        JsonDecodingTestAction.model_validate(fixed_data)

    # Should fail validation because "not valid json" can't be parsed as list
    assert "items" in str(exc_info.value)


def test_json_string_with_wrong_type_rejected():
    """Test that JSON strings with wrong types are rejected."""
    # Field expects list but JSON string contains dict
    data = {
        "items": '{"not": "a list"}',
        "config": "{}",
        "name": "test",
    }
    fixed_data = fix_malformed_tool_arguments(data, JsonDecodingTestAction)

    with pytest.raises(ValidationError) as exc_info:
        JsonDecodingTestAction.model_validate(fixed_data)

    assert "items" in str(exc_info.value)


def test_nested_structures():
    """Test that nested lists and dicts in JSON strings work."""

    class NestedAction(Action):
        nested_list: list[list[int]] = Field(description="Nested list")
        nested_dict: dict[str, dict[str, str]] = Field(description="Nested dict")

    data = {
        "nested_list": "[[1, 2], [3, 4]]",
        "nested_dict": '{"outer": {"inner": "value"}}',
    }
    fixed_data = fix_malformed_tool_arguments(data, NestedAction)
    action = NestedAction.model_validate(fixed_data)

    assert action.nested_list == [[1, 2], [3, 4]]
    assert action.nested_dict == {"outer": {"inner": "value"}}


def test_empty_collections():
    """Test that empty lists and dicts work."""
    data = {
        "items": "[]",
        "config": "{}",
        "name": "empty",
    }
    fixed_data = fix_malformed_tool_arguments(data, JsonDecodingTestAction)
    action = JsonDecodingTestAction.model_validate(fixed_data)

    assert action.items == []
    assert action.config == {}


def test_mixed_native_and_json_strings():
    """Test mixing native values and JSON strings in same model."""
    data = {
        "items": ["native", "list"],  # Native list
        "config": '{"from": 1, "json": 2}',  # JSON string
        "name": "mixed",
    }
    fixed_data = fix_malformed_tool_arguments(data, JsonDecodingTestAction)
    action = JsonDecodingTestAction.model_validate(fixed_data)

    assert action.items == ["native", "list"]
    assert action.config == {"from": 1, "json": 2}
    assert action.name == "mixed"


def test_unicode_in_json_strings():
    """Test that unicode characters in JSON strings are handled correctly."""
    data = {
        "items": '["hello", "世界", "🌍"]',
        "config": '{"greeting": 1, "你好": 2}',
        "name": "unicode",
    }
    fixed_data = fix_malformed_tool_arguments(data, JsonDecodingTestAction)
    action = JsonDecodingTestAction.model_validate(fixed_data)

    assert action.items == ["hello", "世界", "🌍"]
    assert action.config == {"greeting": 1, "你好": 2}


def test_whitespace_in_json_strings():
    """Test that JSON strings with extra whitespace work."""
    data = {
        "items": '  [ "a" , "b" , "c" ]  ',
        "config": '  { "x" : 1 , "y" : 2 }  ',
        "name": "whitespace",
    }
    fixed_data = fix_malformed_tool_arguments(data, JsonDecodingTestAction)
    action = JsonDecodingTestAction.model_validate(fixed_data)

    assert action.items == ["a", "b", "c"]
    assert action.config == {"x": 1, "y": 2}
