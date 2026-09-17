"""Cross-package tests pinning SDK_TOOL_EXPERIENCE_MAP to the real registry.

The map in ``openhands.sdk.security.toolshield_helpers`` keys experience
files by the REGISTERED tool names that ``ToolDefinition.__init_subclass__``
derives (snake_case, ``_tool`` suffix dropped). tests/sdk must not import
``openhands.tools``, so the assertions against the actual tool classes and
the default preset live here -- if a tool is renamed or the map keys drift,
these fail instead of the mapping shipping as a silent no-op (the exact
failure mode flagged in review of PR #2911).
"""

from openhands.sdk.security.toolshield_helpers import (
    SDK_TOOL_EXPERIENCE_MAP,
    mcp_tools_from_config,
)
from openhands.tools.browser_use import BrowserToolSet
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.planning_file_editor import PlanningFileEditorTool
from openhands.tools.preset.default import get_default_tools
from openhands.tools.terminal import TerminalTool


def test_map_keys_match_registered_tool_names():
    """Every mapped built-in tool's real ``.name`` must be a map key."""
    for tool_cls, experience in [
        (FileEditorTool, "filesystem-mcp"),
        (PlanningFileEditorTool, "filesystem-mcp"),
        (TerminalTool, "terminal-mcp"),
        (BrowserToolSet, "playwright-mcp"),
    ]:
        assert SDK_TOOL_EXPERIENCE_MAP.get(tool_cls.name) == experience, (
            f"{tool_cls.__name__}.name == {tool_cls.name!r} is not mapped to "
            f"{experience!r}; SDK_TOOL_EXPERIENCE_MAP keys have drifted from "
            "the registered tool names"
        )


def test_default_preset_tool_names_map_to_experiences():
    """The documented pattern ``tool_names=[t.name for t in agent.tools]``
    must map for the default preset: filesystem and playwright experiences
    both appear."""
    names = [t.name for t in get_default_tools()]
    result = mcp_tools_from_config({}, tool_names=names)
    assert "filesystem-mcp" in result
    assert "playwright-mcp" in result
    assert "terminal-mcp" in result
