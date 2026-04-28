"""MCP configuration file loading, variable expansion, and merging.

Handles ``.mcp.json`` discovery, ``${VAR}`` / ``${VAR:-default}`` expansion,
validation via :class:`fastmcp.mcp_config.MCPConfig`, and config merging.
"""

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastmcp.mcp_config import MCPConfig

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# Type alias for secret lookup functions
SecretLookup = Callable[[str], str | None]


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


def _serialize_for_json(obj: object) -> object:
    """Recursively convert Pydantic models to dicts for JSON serialization.

    This handles the case where MCP config contains Pydantic model objects
    (RemoteMCPServer, StdioMCPServer) instead of plain dicts.
    """
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    elif isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_serialize_for_json(item) for item in obj]
    return obj


def expand_mcp_variables(
    config: dict[str, Any],
    variables: dict[str, str],
    get_secret: SecretLookup | None = None,
    *,
    expand_defaults: bool = True,
) -> dict[str, Any]:
    """Expand variables in MCP configuration.

    Supports variable expansion similar to Claude Code:
    - ${VAR} - Environment variables, provided variables, or secrets
    - ${VAR:-default} - With default value

    Resolution order:
    1. Provided variables (e.g., SKILL_ROOT)
    2. Secrets (via get_secret callback, if provided)
    3. Environment variables
    4. Default value (if specified and expand_defaults=True)

    Args:
        config: MCP configuration dictionary. May contain Pydantic model
            objects (e.g., RemoteMCPServer, StdioMCPServer) which will be
            converted to dicts before JSON serialization.
        variables: Dictionary of variable names to values (e.g., SKILL_ROOT).
        get_secret: Callback to look up a secret by name. We use a callback
            rather than a dict to avoid extracting all secrets into plain text.
            Pass ``secret_registry.get_secret_value`` or ``{"K": "V"}.get``
            for tests.
        expand_defaults: If True, apply default values for unresolved
            variables. If False, preserve ${VAR:-default} as-is for later
            expansion. This allows deferred expansion when secrets are not
            yet available.

    Returns:
        Configuration with variables expanded.
    """
    serializable_config = _serialize_for_json(config)
    config_str = json.dumps(serializable_config)

    var_pattern = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)(?::-([^}]*))?\}")

    def replace_var(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default_value = match.group(2)

        if var_name in variables:
            return variables[var_name]
        if get_secret is not None:
            secret_value = get_secret(var_name)
            if secret_value is not None:
                return secret_value
        if var_name in os.environ:
            return os.environ[var_name]
        if expand_defaults and default_value is not None:
            return default_value
        return match.group(0)

    config_str = var_pattern.sub(replace_var, config_str)
    return json.loads(config_str)


def load_mcp_config(
    mcp_json_path: Path,
    root_dir: Path | None = None,
    get_secret: SecretLookup | None = None,
    *,
    expand_defaults: bool = True,
) -> dict[str, Any]:
    """Load and validate a ``.mcp.json`` file.

    Args:
        mcp_json_path: Path to the ``.mcp.json`` file.
        root_dir: Root directory of the extension (exposed as
            ``${SKILL_ROOT}`` during variable expansion).
        get_secret: Optional callback to look up per-conversation secrets.
            See :func:`expand_mcp_variables` for details.
        expand_defaults: If True, apply default values for unresolved
            variables. If False, preserve ``${VAR:-default}`` as-is for
            later expansion. Use False during plugin loading to defer
            until secrets are available.

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

    config = expand_mcp_variables(
        config,
        variables,
        get_secret=get_secret,
        expand_defaults=expand_defaults,
    )

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
        A new merged dict. Empty dict if both inputs are ``None``.
    """
    if base is None and override is None:
        return {}
    if base is None:
        return dict(override) if override else {}
    if override is None:
        return dict(base)

    result = dict(base)

    if "mcpServers" in override:
        existing_servers = result.get("mcpServers", {})
        for server_name in override["mcpServers"]:
            if server_name in existing_servers:
                logger.warning("MCP server '%s' overridden during merge", server_name)
        result["mcpServers"] = {
            **existing_servers,
            **override["mcpServers"],
        }

    for key, value in override.items():
        if key != "mcpServers":
            if key in result:
                logger.warning("MCP config key '%s' overridden during merge", key)
            result[key] = value

    return result
