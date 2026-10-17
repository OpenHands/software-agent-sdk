"""Compatibility helpers for MCP SDK field renames across fastmcp versions.

fastmcp 4.0 renamed several `mcp.types` fields to snake_case (``Tool.inputSchema`` ->
``input_schema``, ``CallToolResult.isError`` -> ``is_error``,
``ImageContent.mimeType`` -> ``mime_type``), keeping the old name as a
deprecated compat property that logs a ``FastMCPDeprecationWarning`` on every access.

We support ``fastmcp>=3.2.0`` (see openhands-sdk/pyproject.toml), where only the old
name exists at all -- so the code can't just switch to the new name outright without
breaking on the pinned floor, and reading the old name unconditionally means every
caller on a newer fastmcp gets a deprecation warning on every tool call. Read whichever
name the installed version actually provides instead of assuming either one.
"""

from typing import Any


_MISSING = object()


def compat_attr(obj: object, new_name: str, old_name: str) -> Any:
    """Return ``getattr(obj, new_name)`` if present, else ``getattr(obj, old_name)``."""
    val = getattr(obj, new_name, _MISSING)
    return val if val is not _MISSING else getattr(obj, old_name)
