"""Tests for conversation tags validation and integration."""

import pytest
from pydantic import ValidationError

from openhands.sdk.conversation.types import (
    TAG_VALUE_MAX_LENGTH,
    ConversationTags,
    _validate_tags,
)


def test_validate_tags_valid():
    tags = {"env": "production", "team": "backend", "priority": "high"}
    result = _validate_tags(tags)
    assert result == tags


def test_validate_tags_none_returns_empty():
    assert _validate_tags(None) == {}


def test_validate_tags_empty_dict():
    assert _validate_tags({}) == {}


def test_validate_tags_invalid_key_uppercase():
    with pytest.raises(ValueError, match="lowercase alphanumeric"):
        _validate_tags({"Env": "prod"})


def test_validate_tags_invalid_key_with_hyphen():
    with pytest.raises(ValueError, match="lowercase alphanumeric"):
        _validate_tags({"my-key": "value"})


def test_validate_tags_invalid_key_with_underscore():
    with pytest.raises(ValueError, match="lowercase alphanumeric"):
        _validate_tags({"my_key": "value"})


def test_validate_tags_invalid_key_with_spaces():
    with pytest.raises(ValueError, match="lowercase alphanumeric"):
        _validate_tags({"my key": "value"})


def test_validate_tags_value_max_length():
    long_value = "x" * TAG_VALUE_MAX_LENGTH
    result = _validate_tags({"key": long_value})
    assert result["key"] == long_value


def test_validate_tags_value_exceeds_max_length():
    long_value = "x" * (TAG_VALUE_MAX_LENGTH + 1)
    with pytest.raises(ValueError, match="exceeds maximum length"):
        _validate_tags({"key": long_value})


def test_validate_tags_numeric_key():
    result = _validate_tags({"123": "value"})
    assert result == {"123": "value"}


def test_validate_tags_alphanumeric_key():
    result = _validate_tags({"abc123": "value"})
    assert result == {"abc123": "value"}


def test_tags_in_pydantic_model():
    """Test that ConversationTags works as a Pydantic field type."""
    from pydantic import BaseModel

    class TestModel(BaseModel):
        tags: ConversationTags = {}

    # Valid tags
    m = TestModel(tags={"env": "prod"})
    assert m.tags == {"env": "prod"}

    # None coerced to empty dict by the BeforeValidator
    m = TestModel.model_validate({"tags": None})
    assert m.tags == {}

    # Invalid key rejected
    with pytest.raises(ValidationError):
        TestModel(tags={"BAD": "value"})
