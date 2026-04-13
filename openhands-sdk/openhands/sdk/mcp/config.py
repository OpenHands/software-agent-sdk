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


def merge_mcp_configs(
    base: dict[str, Any] | None,
    override: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge two MCP configuration dicts with last-wins semantics.

    Merge rules (Claude Code compatible):
    - ``mcpServers``: deep-merge by server name (override wins per server)
    - Other top-level keys: shallow override (override wins)

    Neither input dict is mutated.

    Args:
        base: Existing MCP config (may be ``None`` or empty).
        override: MCP config to layer on top (may be ``None`` or empty).

    Returns:
        A new merged dict.  Empty dict if both inputs are ``None``.
    """
    if base is None and override is None:
        return {}
    if base is None:
        return dict(override) if override else {}
    if override is None:
        return dict(base)

    result = dict(base)

    # Merge mcpServers by server name
    if "mcpServers" in override:
        existing_servers = result.get("mcpServers", {})
        for server_name in override["mcpServers"]:
            if server_name in existing_servers:
                logger.warning("MCP server '%s' overridden during merge", server_name)
        result["mcpServers"] = {
            **existing_servers,
            **override["mcpServers"],
        }

    # Other top-level keys: override wins
    for key, value in override.items():
        if key != "mcpServers":
            if key in result:
                logger.warning("MCP config key '%s' overridden during merge", key)
            result[key] = value

    return result
