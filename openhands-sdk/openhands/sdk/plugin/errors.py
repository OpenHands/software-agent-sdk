"""Structured errors for the plugin subsystem.

These exceptions carry enough context for the agent-server to return
actionable HTTP responses (plugin name, failure reason, remediation, error_id)
instead of opaque 500s.
"""

from __future__ import annotations

import uuid
from typing import Final


class PluginManifestError(ValueError):
    """A plugin manifest failed validation in a way that should be actionable.

    This is a ``ValueError`` subclass so existing ``except ValueError`` handlers
    keep working, but it carries structured fields the agent-server can serialize
    into an HTTP 400 response with a ``plugin_name``, ``failure_reason``,
    ``remediation`` and ``error_id``.

    Attributes:
        plugin_name: The plugin's name (from the manifest, or the directory name
            as a fallback).
        failure_reason: A short, user-readable description of what went wrong.
        remediation: A short, user-readable suggestion for how to fix it.
        error_id: A UUID for support correlation.
    """

    def __init__(
        self,
        plugin_name: str,
        failure_reason: str,
        remediation: str,
        error_id: str | None = None,
    ) -> None:
        self.plugin_name: Final[str] = plugin_name
        self.failure_reason: Final[str] = failure_reason
        self.remediation: Final[str] = remediation
        self.error_id: Final[str] = error_id or str(uuid.uuid4())
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        return (
            f"Plugin '{self.plugin_name}' failed to load: {self.failure_reason}. "
            f"Remediation: {self.remediation} "
            f"[error_id: {self.error_id}]"
        )

    def to_dict(self) -> dict[str, str]:
        """Serialize for HTTP response bodies."""
        return {
            "plugin_name": self.plugin_name,
            "failure_reason": self.failure_reason,
            "remediation": self.remediation,
            "error_id": self.error_id,
        }


class PluginNameError(ValueError):
    """A plugin name contains unsafe characters (shell metacharacters, paths)."""

    def __init__(self, plugin_name: str, error_id: str | None = None) -> None:
        self.plugin_name: Final[str] = plugin_name
        self.error_id: Final[str] = error_id or str(uuid.uuid4())
        super().__init__(
            f"Plugin name '{plugin_name}' contains unsafe characters. "
            f"Use only alphanumeric characters, hyphens, and underscores. "
            f"[error_id: {self.error_id}]"
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "plugin_name": self.plugin_name,
            "error_id": self.error_id,
        }
