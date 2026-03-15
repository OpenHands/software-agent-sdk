"""Tests for sanitize_json_control_chars helper function.

This module tests the sanitize_json_control_chars helper that escapes raw
control characters (U+0000–U+001F) in JSON strings produced by LLMs.  Some
models (e.g. kimi-k2.5, minimax-m2.5) emit literal control bytes instead of
legal two-character JSON escape sequences, which causes json.loads() to fail.
"""

import json

from openhands.sdk.agent.utils import sanitize_json_control_chars


def test_valid_json_unchanged():
    """Already-valid JSON is returned unmodified."""
    raw = '{"command": "echo hello", "path": "/tmp"}'
    assert sanitize_json_control_chars(raw) == raw


def test_literal_newline_escaped():
    """A raw 0x0A byte inside a JSON string is replaced with \\n."""
    raw = '{"command": "line1\nline2"}'
    sanitized = sanitize_json_control_chars(raw)
    assert "\n" not in sanitized
    parsed = json.loads(sanitized)
    assert parsed["command"] == "line1\nline2"


def test_literal_tab_escaped():
    """A raw 0x09 byte inside a JSON string is replaced with \\t."""
    raw = '{"indent": "col1\tcol2"}'
    sanitized = sanitize_json_control_chars(raw)
    assert "\t" not in sanitized
    parsed = json.loads(sanitized)
    assert parsed["indent"] == "col1\tcol2"


def test_multiple_control_chars():
    """Multiple different control characters are all escaped."""
    raw = '{"text": "a\tb\nc\rd"}'
    sanitized = sanitize_json_control_chars(raw)
    parsed = json.loads(sanitized)
    assert parsed["text"] == "a\tb\nc\rd"


def test_null_byte_escaped():
    """A raw NUL (0x00) byte is escaped to \\u0000."""
    raw = '{"data": "before\x00after"}'
    sanitized = sanitize_json_control_chars(raw)
    assert "\\u0000" in sanitized
    parsed = json.loads(sanitized)
    assert parsed["data"] == "before\x00after"


def test_form_feed_and_backspace():
    """Form-feed and backspace get their short escape aliases."""
    raw = '{"x": "a\x08b\x0cc"}'
    sanitized = sanitize_json_control_chars(raw)
    assert "\\b" in sanitized
    assert "\\f" in sanitized
    parsed = json.loads(sanitized)
    assert parsed["x"] == "a\x08b\x0cc"


def test_already_escaped_sequences_preserved():
    """Properly escaped sequences (\\n, \\t) are NOT double-escaped."""
    raw = r'{"command": "echo \"hello\\nworld\""}'
    sanitized = sanitize_json_control_chars(raw)
    # Already-valid escape sequences should parse correctly
    parsed = json.loads(sanitized)
    assert "hello\\nworld" in parsed["command"]


def test_empty_string():
    """Empty input returns empty output."""
    assert sanitize_json_control_chars("") == ""


def test_realistic_tool_call_arguments():
    """Simulates a realistic malformed tool_call.arguments from an LLM."""
    # The LLM emitted a literal newline inside the "command" value
    raw = '{"command": "cd /workspace && \\\npython test.py", "path": "/workspace"}'
    sanitized = sanitize_json_control_chars(raw)
    parsed = json.loads(sanitized)
    assert "python test.py" in parsed["command"]
    assert parsed["path"] == "/workspace"
