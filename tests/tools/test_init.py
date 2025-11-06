"""Tests for openhands.tools package initialization and import handling."""

import pytest


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


def test_tools_module_has_no_direct_exports():
    """Accessing tools via openhands.tools should fail."""
    import openhands.tools

    assert not hasattr(openhands.tools, "TerminalTool")
    with pytest.raises(AttributeError):
        _ = openhands.tools.TerminalTool  # type: ignore[attr-defined]


def test_from_import_raises_import_error():
    """`from openhands.tools import X` should fail fast."""

    with pytest.raises(ImportError):
        from openhand.tools import TerminalTool  # type: ignore[import] # noqa: F401
