import pytest

from openhands.tools.file_editor.exceptions import (
    EditorToolParameterInvalidError,
    EditorToolParameterMissingError,
    ToolError,
)


def test_tool_error():
    """Test ToolError raises with correct message."""
    with pytest.raises(ToolError) as exc_info:
        raise ToolError("A tool error occurred")
    assert str(exc_info.value) == "A tool error occurred"


def test_editor_tool_parameter_missing_error():
    """Test EditorToolParameterMissingError for missing parameter error message.

    The base message is preserved as an exact prefix; hinted pairs append a
    recovery hint after it (see test below).
    """
    command = "str_replace"
    parameter = "old_str"
    with pytest.raises(EditorToolParameterMissingError) as exc_info:
        raise EditorToolParameterMissingError(command, parameter)
    assert exc_info.value.command == command
    assert exc_info.value.parameter == parameter
    assert exc_info.value.message.startswith(
        f"Parameter `{parameter}` is required for command: {command}."
    )


def test_editor_tool_parameter_invalid_error_with_hint():
    """Test EditorToolParameterInvalidError with hint."""
    parameter = "timeout"
    value = -10
    hint = "Must be a positive integer."
    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        raise EditorToolParameterInvalidError(parameter, str(value), hint)
    assert exc_info.value.parameter == parameter
    assert exc_info.value.value == str(value)
    assert exc_info.value.message == f"Invalid `{parameter}` parameter: {value}. {hint}"


def test_editor_tool_parameter_invalid_error_without_hint():
    """Test EditorToolParameterInvalidError without hint."""
    parameter = "timeout"
    value = -10
    with pytest.raises(EditorToolParameterInvalidError) as exc_info:
        raise EditorToolParameterInvalidError(parameter, str(value))
    assert exc_info.value.parameter == parameter
    assert exc_info.value.value == str(value)
    assert exc_info.value.message == f"Invalid `{parameter}` parameter: {value}."


def test_editor_tool_parameter_missing_error_hint_for_known_pair():
    """Known (command, parameter) pairs append a recovery hint.

    The base message must remain an exact prefix (other code and eval
    harnesses grep for it), with the hint appended after a blank line.
    """
    command = "str_replace"
    parameter = "old_str"
    with pytest.raises(EditorToolParameterMissingError) as exc_info:
        raise EditorToolParameterMissingError(command, parameter)
    assert exc_info.value.command == command
    assert exc_info.value.parameter == parameter
    # Base message is preserved as a prefix.
    assert exc_info.value.message.startswith(
        f"Parameter `{parameter}` is required for command: {command}."
    )
    # The hint teaches a concrete recovery path.
    assert "\n\nHint: " in exc_info.value.message
    assert "`view`" in exc_info.value.message
    assert "`insert`" in exc_info.value.message
    assert "`create`" in exc_info.value.message


@pytest.mark.parametrize(
    "command,parameter,fragment",
    [
        ("str_replace", "old_str", "EXACTLY"),
        ("str_replace", "new_str", "empty string"),
        ("create", "file_text", "str_replace"),
        ("insert", "insert_line", "line number"),
        ("insert", "new_str", "after `insert_line`"),
    ],
)
def test_all_hinted_pairs_have_guidance(
    command: str, parameter: str, fragment: str
) -> None:
    with pytest.raises(EditorToolParameterMissingError) as exc_info:
        raise EditorToolParameterMissingError(command, parameter)
    assert "Hint: " in exc_info.value.message
    assert fragment in exc_info.value.message


def test_unknown_pair_gets_base_message_only():
    """Pairs without a hint keep the exact base message (no trailing text)."""
    exc = EditorToolParameterMissingError("undo_edit", "some_param")
    assert exc.message == "Parameter `some_param` is required for command: undo_edit."
