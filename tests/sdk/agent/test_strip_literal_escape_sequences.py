"""Tests for strip_literal_escape_sequences helper function.

Verifies that literal backslash-letter escape sequences (``\\n``, ``\\t``,
``\\r``) appearing *outside* of JSON string values are replaced with spaces,
while identical sequences inside strings are left untouched.
"""

import json

from openhands.sdk.agent.utils import strip_literal_escape_sequences


def test_valid_json_unchanged():
    """Already-valid JSON passes through unmodified."""
    raw = '{"command": "echo hello", "path": "/tmp"}'
    assert strip_literal_escape_sequences(raw) == raw


def test_literal_newline_outside_string():
    r"""Bare ``\n`` between values is replaced with a space."""
    raw = r'{"view_range": \n[2142, 2250]\n\n}'
    result = strip_literal_escape_sequences(raw)
    parsed = json.loads(result)
    assert parsed["view_range"] == [2142, 2250]


def test_qwen_realistic_example():
    r"""Full realistic payload from Qwen3.5-Flash (issue #2488)."""
    raw = (
        r'{"command": "view", "path": "/workspace/django/query.py",'
        r' "view_range": \n[2142, 2250]\n\n}'
    )
    result = strip_literal_escape_sequences(raw)
    parsed = json.loads(result)
    assert parsed["command"] == "view"
    assert parsed["path"] == "/workspace/django/query.py"
    assert parsed["view_range"] == [2142, 2250]


def test_escape_inside_string_preserved():
    r"""``\n`` inside a quoted string must NOT be touched."""
    raw = r'{"text": "line1\nline2"}'
    assert strip_literal_escape_sequences(raw) == raw
    parsed = json.loads(raw)
    assert parsed["text"] == "line1\nline2"


def test_escaped_quote_inside_string():
    r"""Escaped quotes inside strings don't confuse the tracker."""
    raw = r'{"text": "say \"hello\nworld\""}'
    assert strip_literal_escape_sequences(raw) == raw


def test_tab_and_return_outside_string():
    r"""Bare ``\t`` and ``\r`` outside strings are also replaced."""
    raw = r'{"a":\t1,\r"b": 2}'
    result = strip_literal_escape_sequences(raw)
    parsed = json.loads(result)
    assert parsed["a"] == 1
    assert parsed["b"] == 2


def test_empty_string():
    assert strip_literal_escape_sequences("") == ""


def test_no_strings_at_all():
    r"""Bare escape in a string with no quotes (degenerate input)."""
    raw = r"\n\t\r"
    result = strip_literal_escape_sequences(raw)
    assert result == "   "
