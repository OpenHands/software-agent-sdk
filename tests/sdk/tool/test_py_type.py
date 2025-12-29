"""Tests for py_type function in openhands.sdk.tool.schema."""

from typing import Any

from openhands.sdk.tool.schema import py_type


class TestPyTypePrimitiveTypes:
    """Test py_type with primitive JSON schema types."""

    def test_string_type(self):
        """Test that string type maps to Python str."""
        # Arrange
        spec = {"type": "string"}

        # Act
        result = py_type(spec)

        # Assert
        assert result is str

    def test_integer_type(self):
        """Test that integer type maps to Python int."""
        # Arrange
        spec = {"type": "integer"}

        # Act
        result = py_type(spec)

        # Assert
        assert result is int

    def test_number_type(self):
        """Test that number type maps to Python float."""
        # Arrange
        spec = {"type": "number"}

        # Act
        result = py_type(spec)

        # Assert
        assert result is float

    def test_boolean_type(self):
        """Test that boolean type maps to Python bool."""
        # Arrange
        spec = {"type": "boolean"}

        # Act
        result = py_type(spec)

        # Assert
        assert result is bool


class TestPyTypeObjectType:
    """Test py_type with object type."""

    def test_object_type(self):
        """Test that object type maps to dict[str, Any]."""
        # Arrange
        spec = {"type": "object"}

        # Act
        result = py_type(spec)

        # Assert
        assert result == dict[str, Any]


class TestPyTypeArrayType:
    """Test py_type with array types."""

    def test_array_without_items(self):
        """Test that array without items returns list[Any]."""
        # Arrange
        spec = {"type": "array"}

        # Act
        result = py_type(spec)

        # Assert
        assert result == list[Any]

    def test_array_with_dict_items(self):
        """Test that array with dict items recursively processes inner type."""
        # Arrange
        spec = {"type": "array", "items": {"type": "string"}}

        # Act
        result = py_type(spec)

        # Assert
        assert result == list[str]

    def test_array_with_nested_array(self):
        """Test that array with nested array processes correctly."""
        # Arrange
        spec = {
            "type": "array",
            "items": {"type": "array", "items": {"type": "integer"}},
        }

        # Act
        result = py_type(spec)

        # Assert
        assert result == list[list[int]]

    def test_array_with_non_dict_items(self):
        """Test that array with non-dict items returns list[Any]."""
        # Arrange
        spec = {"type": "array", "items": "string"}

        # Act
        result = py_type(spec)

        # Assert
        assert result == list[Any]


class TestPyTypeUnionTypes:
    """Test py_type with union types (list/tuple/set)."""

    def test_union_list_with_single_non_null(self):
        """Test that union list with single non-null type extracts that type."""
        # Arrange
        spec = {"type": ["string", "null"]}

        # Act
        result = py_type(spec)

        # Assert
        assert result is str

    def test_union_tuple_with_single_non_null(self):
        """Test that union tuple with single non-null type extracts that type."""
        # Arrange
        spec = {"type": ("integer", "null")}

        # Act
        result = py_type(spec)

        # Assert
        assert result is int

    def test_union_set_with_single_non_null(self):
        """Test that union set with single non-null type extracts that type."""
        # Arrange
        spec = {"type": {"number", "null"}}

        # Act
        result = py_type(spec)

        # Assert
        assert result is float

    def test_union_with_multiple_non_null_types(self):
        """Test that union with multiple non-null types returns Any."""
        # Arrange
        spec = {"type": ["string", "integer"]}

        # Act
        result = py_type(spec)

        # Assert
        assert result is Any

    def test_union_with_only_null(self):
        """Test that union with only null type returns Any."""
        # Arrange
        spec = {"type": ["null"]}

        # Act
        result = py_type(spec)

        # Assert
        assert result is Any

    def test_union_with_three_types_one_null(self):
        """Test that union with three types where one is null extracts non-null."""
        # Arrange
        spec = {"type": ["boolean", "null", "string"]}

        # Act
        result = py_type(spec)

        # Assert
        assert result is Any


class TestPyTypeEdgeCases:
    """Test py_type with edge cases and invalid inputs."""

    def test_missing_type_key(self):
        """Test that missing type key returns Any."""
        # Arrange
        spec = {}

        # Act
        result = py_type(spec)

        # Assert
        assert result is Any

    def test_unknown_type(self):
        """Test that unknown type returns Any."""
        # Arrange
        spec = {"type": "unknown_type"}

        # Act
        result = py_type(spec)

        # Assert
        assert result is Any

    def test_empty_dict(self):
        """Test that empty dict returns Any."""
        # Arrange
        spec = {}

        # Act
        result = py_type(spec)

        # Assert
        assert result is Any

    def test_type_none(self):
        """Test that type=None returns Any."""
        # Arrange
        spec = {"type": None}

        # Act
        result = py_type(spec)

        # Assert
        assert result is Any

    def test_array_with_empty_items_dict(self):
        """Test that array with empty items dict returns list[Any]."""
        # Arrange
        spec = {"type": "array", "items": {}}

        # Act
        result = py_type(spec)

        # Assert
        assert result == list[Any]
