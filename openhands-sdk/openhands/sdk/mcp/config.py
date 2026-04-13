"""MCP configuration file loading and variable expansion.

Handles ``.mcp.json`` discovery, ``${VAR}`` / ``${VAR:-default}`` expansion,
and validation via :class:`fastmcp.mcp_config.MCPConfig`.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fastmcp.mcp_config import MCPConfig

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


def find_mcp_config(directory: Path) -> Path | None:
    """Find ``.mcp.json`` in *directory*.

    Returns:
        Path to the file if it exists, ``None`` otherwise.
    """
    if not directory.is_dir():
        return None
    mcp_json = directory / ".mcp.json"
    if mcp_json.exists() and mcp_json.is_file():
        return mcp_json
    return None


def expand_mcp_variables(
    config: dict[str, Any],
    variables: dict[str, str],
) -> dict[str, Any]:
    """Expand ``${VAR}`` and ``${VAR:-default}`` in an MCP config dict.

    Looks up *variables* first, then ``os.environ``, then falls back to the
    default (if given) or leaves the placeholder as-is.
    """
    config_str = json.dumps(config)
    var_pattern = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)(?::-([^}]*))?\}")

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default_value = match.group(2)
        if var_name in variables:
            return variables[var_name]
        if var_name in os.environ:
            return os.environ[var_name]
        if default_value is not None:
            return default_value
        return match.group(0)

    config_str = var_pattern.sub(_replace, config_str)
    return json.loads(config_str)


def load_mcp_config(
    mcp_json_path: Path,
    root_dir: Path | None = None,
) -> dict[str, Any]:
    """Load and validate a ``.mcp.json`` file.

    Args:
        mcp_json_path: Path to the ``.mcp.json`` file.
        root_dir: Root directory of the extension (exposed as
            ``${SKILL_ROOT}`` during variable expansion).

    Returns:
        Parsed and validated MCP configuration dictionary.

    Raises:
        ValueError: If the file cannot be read, parsed, or fails
            :class:`MCPConfig` validation.
    """
    try:
        with open(mcp_json_path) as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {mcp_json_path}: {e}") from e
    except OSError as e:
        raise ValueError(f"Cannot read {mcp_json_path}: {e}") from e

    if not isinstance(config, dict):
        raise ValueError(
            f"Invalid .mcp.json format: expected object, got {type(config).__name__}"
        )

    variables: dict[str, str] = {}
    if root_dir:
        variables["SKILL_ROOT"] = str(root_dir)

    config = expand_mcp_variables(config, variables)

    try:
        MCPConfig.model_validate(config)
    except Exception as e:
        raise ValueError(f"Invalid MCP configuration: {e}") from e

    return config
