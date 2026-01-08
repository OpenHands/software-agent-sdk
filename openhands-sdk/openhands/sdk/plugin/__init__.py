"""Plugin module for OpenHands SDK.

This module provides support for loading and managing plugins that bundle
skills, hooks, MCP configurations, agents, and commands together.
"""

from openhands.sdk.plugin.fetch import PluginFetchError, parse_plugin_source
from openhands.sdk.plugin.plugin import Plugin
from openhands.sdk.plugin.types import (
    AgentDefinition,
    CommandDefinition,
    PluginAuthor,
    PluginManifest,
)


__all__ = [
    "Plugin",
    "PluginFetchError",
    "PluginManifest",
    "PluginAuthor",
    "AgentDefinition",
    "CommandDefinition",
    "parse_plugin_source",
]
