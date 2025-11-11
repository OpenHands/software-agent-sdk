"""Utility functions for dynamic tool registration on the server."""

import importlib

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# Mapping of tool names to their module paths for dynamic import
TOOL_MODULE_MAP = {
    "terminal": "openhands.tools.terminal",
    "file_editor": "openhands.tools.file_editor",
    "task_tracker": "openhands.tools.task_tracker",
    "browser": "openhands.tools.browser_use",
    "glob": "openhands.tools.glob",
    "grep": "openhands.tools.grep",
    "planning_file_editor": "openhands.tools.planning_file_editor",
}


def register_tools_by_name(tool_names: list[str]) -> None:
    """Dynamically register tools by importing their modules.

    Args:
        tool_names: List of tool names to register.

    Raises:
        ValueError: If a tool name is not recognized or cannot be imported.
    """
    for tool_name in tool_names:
        if tool_name not in TOOL_MODULE_MAP:
            logger.warning(
                f"Tool '{tool_name}' not found in TOOL_MODULE_MAP. "
                "Skipping registration."
            )
            continue

        module_path = TOOL_MODULE_MAP[tool_name]
        try:
            # Import the module to trigger tool auto-registration
            importlib.import_module(module_path)
            logger.debug(f"Tool '{tool_name}' registered via module '{module_path}'")
        except ImportError as e:
            logger.error(
                f"Failed to import module '{module_path}' for tool '{tool_name}': {e}"
            )
            raise ValueError(f"Cannot register tool '{tool_name}': {e}") from e
