"""Runtime tools package.

This is the primary import surface for the published ``openhands-tools``
distribution.

Most tool implementations live in explicit submodules (e.g.
``openhands.tools.terminal``). However, we also provide a small set of
convenience re-exports here for the most common tools and presets.

The curated public surface is tracked via ``__all__`` so CI can detect breaking
changes.

Note: BrowserToolSet is intentionally NOT re-exported here to avoid forcing
downstream consumers (e.g., OpenHands-CLI) to bundle the browser-use package
and its heavy dependencies. Users who need browser tools should import directly
from ``openhands.tools.browser_use``.
"""

from importlib.metadata import PackageNotFoundError, version

from openhands.tools.delegate import DelegationVisualizer, register_agent
from openhands.tools.delegate.registration import get_agent_factory
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.preset.default import (
    get_default_agent,
    get_default_tools,
    register_default_tools,
)
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool


try:
    __version__ = version("openhands-tools")
except PackageNotFoundError:
    __version__ = "0.0.0"  # fallback for editable/unbuilt environments


__all__ = [
    "__version__",
    "DelegationVisualizer",
    "FileEditorTool",
    "TaskTrackerTool",
    "TerminalTool",
    "get_agent_factory",
    "get_default_agent",
    "get_default_tools",
    "register_agent",
    "register_default_tools",
]
