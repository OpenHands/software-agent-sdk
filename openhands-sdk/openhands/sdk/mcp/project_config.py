"""Project-level .mcp.json discovery and loading."""

from pathlib import Path
from typing import Any, Final

from openhands.sdk.context.skills.exceptions import SkillValidationError
from openhands.sdk.context.skills.utils import load_mcp_config
from openhands.sdk.logger import get_logger

logger = get_logger(__name__)

_PROJECT_MCP_CANDIDATES: Final[tuple[str, ...]] = (
    ".openhands/.mcp.json",
    ".mcp.json",
)


def _find_project_mcp_json(project_dir: Path) -> Path | None:
    """Return the first project MCP config path if present.

    Preference order follows ``_PROJECT_MCP_CANDIDATES``.
    """
    for rel in _PROJECT_MCP_CANDIDATES:
        candidate = project_dir / rel
        if candidate.is_file():
            return candidate
    return None


def load_project_mcp_config(project_dir: Path) -> dict[str, Any] | None:
    """Load and validate project ``.mcp.json``.

    Uses ``load_mcp_config`` from skills (variable expansion, ``MCPConfig``
    validation). Returns ``None`` if no file exists, or if the file is
    invalid (logged and ignored).
    """
    path = _find_project_mcp_json(project_dir)
    if path is None:
        return None
    try:
        return load_mcp_config(path, skill_root=project_dir)
    except SkillValidationError as e:
        logger.warning("Ignoring invalid project MCP config at %s: %s", path, e)
        return None
