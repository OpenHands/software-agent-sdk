"""Tests for openhands.tools package initialization and import handling."""


def test_submodule_imports_work():
    """Tools should be imported via explicit submodules."""
    from openhands.tools.browser_use import BrowserToolSet
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.task_tracker import TaskTrackerTool
    from openhands.tools.terminal import TerminalTool

    assert TerminalTool is not None
    assert FileEditorTool is not None
    assert TaskTrackerTool is not None
    assert BrowserToolSet is not None


def test_tools_module_has_expected_top_level_exports():
    """Common tools/presets should be importable from the top-level package.

    Note: BrowserToolSet is intentionally NOT exported at the top level to avoid
    forcing downstream consumers to bundle browser-use and its heavy dependencies.
    See: https://github.com/OpenHands/OpenHands-CLI/pull/527
    """

    import openhands.tools

    assert openhands.tools.TerminalTool is not None
    assert openhands.tools.FileEditorTool is not None
    assert openhands.tools.TaskTrackerTool is not None

    assert openhands.tools.get_default_agent is not None
    assert openhands.tools.get_default_tools is not None
    assert openhands.tools.register_default_tools is not None


def test_from_import_works():
    """`from openhands.tools import X` should work for exported symbols."""

    from openhands.tools import TerminalTool  # noqa: F401
