"""Tests for the Schema immutability contract."""

import pytest
from pydantic import Field, ValidationError

from openhands.sdk.tool.schema import Schema


class MockSchema(Schema):
    value: str = Field(description="Test value")


def test_schema_subclasses_are_frozen():
    schema = MockSchema(value="original")

    with pytest.raises(ValidationError, match="Instance is frozen"):
        schema.value = "changed"
