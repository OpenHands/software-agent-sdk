"""Backward compatibility tests for FileEditorAction and FileEditorObservation.

These tests verify that events serialized in previous SDK versions can still
be loaded correctly. This is critical for production systems that may resume
conversations created with older SDK versions.

IMPORTANT: These tests should NOT be modified to fix unit test failures.
If a test fails, it indicates that the code should be updated to accommodate
the old serialization format, NOT that the test should be changed.

VERSION NAMING CONVENTION: The version in the test name should be the LAST
version where a particular event structure exists. For example, undo_edit
was last present in v1.11.4 and removed after that release.
"""

from openhands.tools.file_editor.definition import (
    FileEditorAction,
    FileEditorObservation,
)


# =============================================================================
# FileEditorAction Backward Compatibility Tests
# =============================================================================


def test_v1_11_4_action_with_undo_edit_command():
    """Verify FileEditorAction with command='undo_edit' loads (last version: v1.11.4).

    undo_edit was removed after v1.11.4. Old events must still deserialize.
    The deprecated command is migrated to 'view'.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """
    old_format = {
        "command": "undo_edit",
        "path": "/workspace/project/src/main.py",
    }
    action = FileEditorAction.model_validate(old_format)
    assert action.command == "view"
    assert action.path == "/workspace/project/src/main.py"


def test_action_current_commands_unaffected():
    """Verify current commands still work after adding deprecation handling."""
    for cmd in ("view", "create", "str_replace", "insert"):
        action = FileEditorAction.model_validate({"command": cmd, "path": "/test.py"})
        assert action.command == cmd


# =============================================================================
# FileEditorObservation Backward Compatibility Tests
# =============================================================================


def test_v1_11_4_observation_with_undo_edit_command():
    """Verify FileEditorObservation with command='undo_edit' loads (last version: v1.11.4).

    undo_edit was removed after v1.11.4. Old events must still deserialize.
    The deprecated command is migrated to 'view'.

    AGENTS: Do NOT modify this test to fix failures. Update the code instead.
    """  # noqa: E501
    old_format = {
        "command": "undo_edit",
        "path": "/workspace/project/src/main.py",
        "prev_exist": True,
        "old_content": "original",
        "new_content": "restored",
        "content": [{"type": "text", "text": "Last edit undone successfully."}],
        "is_error": False,
    }
    obs = FileEditorObservation.model_validate(old_format)
    assert obs.command == "view"
    assert obs.path == "/workspace/project/src/main.py"
    assert obs.old_content == "original"
    assert obs.new_content == "restored"


def test_observation_current_commands_unaffected():
    """Verify current commands still work after adding deprecation handling."""
    for cmd in ("view", "create", "str_replace", "insert"):
        obs = FileEditorObservation.model_validate(
            {
                "command": cmd,
                "content": [{"type": "text", "text": "ok"}],
                "is_error": False,
            }
        )
        assert obs.command == cmd


# =============================================================================
# Mixed old/new event sequence (simulates resumed conversation)
# =============================================================================


def test_mixed_old_and_new_events_in_sequence():
    """Verify a sequence of old and new events all load correctly.

    Simulates resuming a conversation that has a mix of events created
    before and after the undo_edit removal.
    """
    events = [
        # Old: undo_edit action
        (
            FileEditorAction,
            {"command": "undo_edit", "path": "/src/a.py"},
        ),
        # New: str_replace action
        (
            FileEditorAction,
            {
                "command": "str_replace",
                "path": "/src/a.py",
                "old_str": "foo",
                "new_str": "bar",
            },
        ),
        # Old: undo_edit observation
        (
            FileEditorObservation,
            {
                "command": "undo_edit",
                "content": [{"type": "text", "text": "undone"}],
                "is_error": False,
            },
        ),
        # New: str_replace observation
        (
            FileEditorObservation,
            {
                "command": "str_replace",
                "content": [{"type": "text", "text": "edited"}],
                "is_error": False,
            },
        ),
    ]
    for cls, data in events:
        obj = cls.model_validate(data)
        assert obj is not None
