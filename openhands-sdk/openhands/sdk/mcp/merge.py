"""Merge MCP configuration dictionaries."""

from typing import Any


def merge_mcp_configs(
    base: dict[str, Any] | None,
    overlay: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge two MCP config dicts; overlay wins on key conflicts.

    ``mcpServers`` entries are merged by server name; other top-level keys use
    shallow overlay semantics (overlay wins).
    """
    match (base, overlay):
        case (None, None):
            return {}
        case (None, _):
            return dict(overlay)
        case (_, None):
            return dict(base)

    result = {**base, **overlay}
    if "mcpServers" in base and "mcpServers" in overlay:
        result["mcpServers"] = {**base["mcpServers"], **overlay["mcpServers"]}
    return result
