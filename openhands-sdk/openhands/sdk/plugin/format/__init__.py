"""Plugin format strategies.

A *plugin format* owns everything that is specific to how a plugin is laid out
on disk: where its manifest lives and how it is validated, which file its MCP
config is read from and how variables in it are expanded, and how its client
extensions (commands / agents / hooks) are located. Each format's job is to read
a plugin directory and produce a normalized :class:`~openhands.sdk.plugin.Plugin`.

Everything *downstream* of a loaded plugin (merging skills into an agent context,
merging MCP servers, concatenating hooks) is format-agnostic and lives on
``Plugin`` itself, so adding a new format never touches the merge/apply path.

Module layout:

- ``base`` — the abstract :class:`PluginFormat` contract plus the discovery
  logic shared by every format (skills discovery, final assembly).
- ``claude_code`` — the concrete :class:`ClaudeCodePluginFormat` strategy.
- this package ``__init__`` — the format registry (``_FORMATS``) and the
  :func:`detect_format` dispatcher.

Detection precedence (:func:`detect_format`): the first format in ``_FORMATS``
whose :meth:`PluginFormat.detect` returns True wins. Only the Claude Code format
is implemented today, and its ``detect`` accepts any directory, so it is the
universal fallback (including when no manifest is present at all). The Agent
Plugins format (agent-plugins.org) is a planned follow-up and is why this seam
exists; when added it will sit ahead of Claude Code and claim directories with a
root-level ``plugin.json``.

How to add a new plugin format
------------------------------
The strategy is deliberately fill-in-the-blank. To add a format:

1. Create a new module in this package and subclass :class:`PluginFormat`, giving
   it a unique ``name`` (used in logs).
2. Implement :meth:`PluginFormat.detect` — return True only for directories this
   format should claim. Keep it cheap (a filesystem probe), and make it specific
   so it does not shadow other formats.
3. Implement the format-specific loaders: :meth:`~PluginFormat.load_manifest`,
   :meth:`~PluginFormat.load_mcp_config`, :meth:`~PluginFormat.load_hooks`,
   :meth:`~PluginFormat.load_agents`, and :meth:`~PluginFormat.load_commands`.
   Each is responsible for exactly one component type and should isolate failures
   to that component (skip/disable it, never abort the whole plugin).
4. Reuse the base as-is where behavior is shared. In particular
   :meth:`~PluginFormat.load_skills` implements the ``skills/<name>/SKILL.md``
   discovery rule, which is identical across formats — do not re-implement it
   unless your format genuinely differs. The shared :meth:`~PluginFormat.load`
   orchestrates all of the above into a normalized ``Plugin``; you should not
   need to override it.
5. Register the class in ``_FORMATS`` below, in the position that reflects its
   detection precedence (earlier = higher priority). Fallback formats go last.
6. Add a test asserting :func:`detect_format` selects your format for a directory
   it should claim and does not for one it should not (see
   ``tests/sdk/plugin/test_plugin_loading.py::TestDetectFormat``).

Because every format produces the same normalized ``Plugin``, none of this
touches the merge/apply path or any existing call site.
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
