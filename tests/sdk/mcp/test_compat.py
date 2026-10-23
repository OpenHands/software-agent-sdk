"""Tests for the fastmcp field-rename compat helper (see mcp/_compat.py)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from openhands.sdk.mcp._compat import compat_attr


def test_compat_attr_prefers_the_new_name_when_present():
    obj = SimpleNamespace(input_schema={"type": "object"}, inputSchema="stale")
    assert compat_attr(obj, "input_schema", "inputSchema") == {"type": "object"}


def test_compat_attr_falls_back_to_the_old_name_when_new_is_absent():
    obj = SimpleNamespace(inputSchema={"type": "object"})
    assert compat_attr(obj, "input_schema", "inputSchema") == {"type": "object"}


def test_compat_attr_falls_back_on_a_spec_restricted_mock_without_the_new_name():
    # MagicMock(spec=...) raises AttributeError for anything outside the spec,
    # same as a real object built against an older fastmcp that lacks the field.
    class _OldStyleTool:
        inputSchema: dict

    mock = MagicMock(spec=_OldStyleTool)
    mock.inputSchema = {"type": "object"}
    assert compat_attr(mock, "input_schema", "inputSchema") == {"type": "object"}


def test_compat_attr_raises_when_neither_name_exists():
    obj = SimpleNamespace()
    try:
        compat_attr(obj, "input_schema", "inputSchema")
    except AttributeError:
        return
    raise AssertionError("expected AttributeError when neither attribute exists")
