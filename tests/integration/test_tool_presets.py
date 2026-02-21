"""Tests for the tool preset selection logic in integration tests."""

import pytest

from tests.integration.base import ToolPresetType, get_tools_for_preset


def test_get_tools_for_preset_default():
    """Test that default preset returns expected tools."""
    tools = get_tools_for_preset("default", enable_browser=False)
    tool_names = {t.name for t in tools}

    assert "terminal" in tool_names
    assert "file_editor" in tool_names
    assert "task_tracker" in tool_names
    # Browser tools should not be present
    assert "browser_navigate" not in tool_names


def test_get_tools_for_preset_default_with_browser():
    """Test that default preset with browser enabled includes browser tools.

    Note: This test is skipped during integration test runs because browser
    tools cause process cleanup issues with ProcessPoolExecutor. The browser
    functionality itself works, but cleanup during parallel test execution hangs.
    """
    pytest.skip(
        "Browser tools disabled in integration tests due to ProcessPoolExecutor "
        "cleanup issues - see issue #2124"
    )


def test_get_tools_for_preset_gemini():
    """Test that gemini preset returns gemini-style file editing tools."""
    tools = get_tools_for_preset("gemini", enable_browser=False)
    tool_names = {t.name for t in tools}

    assert "terminal" in tool_names
    assert "read_file" in tool_names
    assert "write_file" in tool_names
    assert "edit" in tool_names
    assert "list_directory" in tool_names
    assert "task_tracker" in tool_names
    # Default file_editor should NOT be present
    assert "file_editor" not in tool_names


def test_get_tools_for_preset_gpt5():
    """Test that gpt5 preset returns apply_patch tool."""
    tools = get_tools_for_preset("gpt5", enable_browser=False)
    tool_names = {t.name for t in tools}

    assert "terminal" in tool_names
    assert "apply_patch" in tool_names
    assert "task_tracker" in tool_names
    # Default file_editor should NOT be present
    assert "file_editor" not in tool_names


def test_get_tools_for_preset_planning():
    """Test that planning preset returns read-only tools."""
    tools = get_tools_for_preset("planning", enable_browser=False)
    tool_names = {t.name for t in tools}

    assert "glob" in tool_names
    assert "grep" in tool_names
    assert "planning_file_editor" in tool_names
    # Default file_editor should NOT be present
    assert "file_editor" not in tool_names
    # Browser tools should not be present (planning is read-only)
    assert "browser_navigate" not in tool_names


def test_get_tools_for_preset_invalid():
    """Test that invalid preset raises ValueError."""
    with pytest.raises(ValueError, match="Unknown `preset` parameter"):
        # type: ignore is used here intentionally to test runtime behavior
        get_tools_for_preset("invalid_preset", enable_browser=False)  # type: ignore[arg-type]


def test_tool_preset_type_literal_values():
    """Verify ToolPresetType includes all expected values."""
    # This is a compile-time check but we document expected values here
    valid_presets: list[ToolPresetType] = ["default", "gemini", "gpt5", "planning"]
    for preset in valid_presets:
        # Should not raise
        tools = get_tools_for_preset(preset, enable_browser=False)
        assert len(tools) > 0
