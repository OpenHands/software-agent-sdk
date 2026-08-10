"""Plugin format strategies.

A *plugin format* owns everything specific to how a plugin is laid out on disk
(manifest location and validation, MCP config file and variable expansion, and
where client extensions — commands / agents / hooks — live) and turns a plugin
directory into a normalized :class:`~openhands.sdk.plugin.Plugin`. Everything
downstream of a loaded plugin is format-agnostic and lives on ``Plugin`` itself,
so adding a new format never touches the merge/apply path.

Module layout:

- ``base`` — the abstract :class:`PluginFormat` contract plus the shared
  discovery logic (skills discovery, final assembly).
- ``claude_code`` — the concrete :class:`ClaudeCodePluginFormat` strategy.
- this package ``__init__`` — the format registry (``_FORMATS``) and the
  :func:`detect_format` dispatcher.

:func:`detect_format` returns the first format in ``_FORMATS`` whose
:meth:`PluginFormat.detect` returns True. Claude Code is the only format today
and its ``detect`` accepts any directory, so it is the universal fallback. The
Agent Plugins format (agent-plugins.org) is the planned follow-up this seam
exists for; when added it will sit ahead of Claude Code and claim directories
with a root-level ``plugin.json``.

How to add a new plugin format
------------------------------
1. Create a module in this package and subclass :class:`PluginFormat`, giving it
   a unique class-level ``name`` (used in logs).
2. Implement :meth:`PluginFormat.detect` — cheap, specific, and True only for
   directories this format should claim.
3. Implement the format-specific loaders (:meth:`~PluginFormat.load_manifest`,
   :meth:`~PluginFormat.load_mcp_config`, :meth:`~PluginFormat.load_hooks`,
   :meth:`~PluginFormat.load_agents`, :meth:`~PluginFormat.load_commands`). Each
   owns one component type and should isolate its own failures rather than abort
   the whole plugin.
4. Reuse the base where behavior is shared — notably
   :meth:`~PluginFormat.load_skills` (the ``skills/<name>/SKILL.md`` rule) and
   :meth:`~PluginFormat.load` (final assembly); you should not need to override
   either.
5. Register the class in ``_FORMATS`` below in detection-precedence order
   (earlier = higher priority; fallbacks last).
6. Add a :func:`detect_format` selection test (see
   ``tests/sdk/plugin/test_plugin_loading.py::TestDetectFormat``).
"""

from __future__ import annotations

from pathlib import Path

from openhands.sdk.logger import get_logger
from openhands.sdk.plugin.format.base import PluginFormat
from openhands.sdk.plugin.format.claude_code import ClaudeCodePluginFormat


logger = get_logger(__name__)


# Registered formats, in detection-precedence order. Higher-precedence formats
# come first; the Claude Code format is last because it accepts any directory.
# The Agent Plugins format (root plugin.json, closed schema, mcp.json) will be
# inserted ahead of Claude Code here in a follow-up.
_FORMATS: list[type[PluginFormat]] = [ClaudeCodePluginFormat]


def detect_format(plugin_dir: Path) -> PluginFormat:
    """Select the plugin format for ``plugin_dir``.

    Precedence: the first registered format whose ``detect()`` returns True. The
    Claude Code format matches unconditionally, so this always resolves.
    """
    for fmt_cls in _FORMATS:
        if fmt_cls.detect(plugin_dir):
            logger.debug(f"Detected plugin format '{fmt_cls.name}' for {plugin_dir}")
            return fmt_cls()
    # Unreachable while ClaudeCodePluginFormat.detect() returns True, but keep an
    # explicit, actionable error rather than an implicit None if that changes.
    raise ValueError(f"No plugin format matched {plugin_dir}")


__all__ = [
    "PluginFormat",
    "ClaudeCodePluginFormat",
    "detect_format",
]
