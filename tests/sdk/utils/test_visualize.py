"""Tests for openhands.sdk.utils.visualize module."""

from rich.text import Text

from openhands.sdk.utils.visualize import display_json


def test_display_json_with_dictionary():
    """Test display_json with a dictionary input."""
    data = {"key1": "value1", "key2": 42, "key3": None}
    result = display_json(data)

    assert isinstance(result, Text)
    text_content = str(result)
    assert "key1" in text_content
    assert "value1" in text_content
    assert "key2" in text_content
    assert "42" in text_content
    # None fields should be skipped
    assert "key3" not in text_content


def test_display_json_with_list():
    """Test display_json with a list input (this was the bug)."""
    data = ["item1", "item2", 42, True]
    result = display_json(data)

    assert isinstance(result, Text)
    text_content = str(result)
    assert "[List with 4 items]" in text_content
    assert "[0]" in text_content
    assert "item1" in text_content
    assert "[1]" in text_content
    assert "item2" in text_content
    assert "[2]" in text_content
    assert "42" in text_content
    assert "[3]" in text_content
    assert "True" in text_content


def test_display_json_with_string():
    """Test display_json with a string input."""
    data = "simple string"
    result = display_json(data)

    assert isinstance(result, Text)
    text_content = str(result)
    assert '"simple string"' in text_content


def test_display_json_with_multiline_string():
    """Test display_json with a multiline string input."""
    data = "line1\nline2\nline3"
    result = display_json(data)

    assert isinstance(result, Text)
    text_content = str(result)
    assert "String:" in text_content
    assert "line1" in text_content
    assert "line2" in text_content
    assert "line3" in text_content


def test_display_json_with_number():
    """Test display_json with a number input."""
    data = 42
    result = display_json(data)

    assert isinstance(result, Text)
    text_content = str(result)
    assert "42" in text_content


def test_display_json_with_float():
    """Test display_json with a float input."""
    data = 3.14159
    result = display_json(data)

    assert isinstance(result, Text)
    text_content = str(result)
    assert "3.14159" in text_content


def test_display_json_with_boolean():
    """Test display_json with a boolean input."""
    data = True
    result = display_json(data)

    assert isinstance(result, Text)
    text_content = str(result)
    assert "True" in text_content

    data = False
    result = display_json(data)

    assert isinstance(result, Text)
    text_content = str(result)
    assert "False" in text_content


def test_display_json_with_none():
    """Test display_json with None input."""
    data = None
    result = display_json(data)

    assert isinstance(result, Text)
    text_content = str(result)
    assert "null" in text_content


def test_display_json_with_nested_structures():
    """Test display_json with nested dictionaries and lists."""
    data = {
        "simple": "value",
        "nested_dict": {"inner": "data"},
        "list_data": [1, 2, 3],
        "multiline": "line1\nline2\nline3",
    }
    result = display_json(data)

    assert isinstance(result, Text)
    text_content = str(result)
    assert "simple" in text_content
    assert "nested_dict" in text_content
    assert "list_data" in text_content
    assert "multiline" in text_content
