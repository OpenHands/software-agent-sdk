"""Project-level .mcp.json discovery and loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openhands.sdk.context.skills.exceptions import SkillValidationError
from openhands.sdk.context.skills.utils import load_mcp_config
from openhands.sdk.logger import get_logger

logger = get_logger(__name__)


def find_project_mcp_json(project_dir: Path) -> Path | None:
    """Return the first project MCP config path if present.

    Preference order: ``.openhands/.mcp.json``, then root ``.mcp.json``.
    """
    for candidate in (
        project_dir / ".openhands" / ".mcp.json",
        project_dir / ".mcp.json",
    ):
        if candidate.is_file():
            return candidate
    return None


def try_load_project_mcp_config(project_dir: Path) -> dict[str, Any] | None:
    """Load and validate project ``.mcp.json``, or return None if missing or invalid."""
    path = find_project_mcp_json(project_dir)
    if path is None:
        return None
    try:
        return load_mcp_config(path, skill_root=project_dir)
    except SkillValidationError as e:
        logger.warning("Ignoring invalid project MCP config at %s: %s", path, e)
        return None
