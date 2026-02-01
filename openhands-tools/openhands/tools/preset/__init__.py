"""
Agent presets for OpenHands SDK.

This package provides predefined agent configurations (tool bundles)
that can be used out of the box. Presets are intended as starting points
for common use cases, such as a default production agent with shell access,
file editing, task tracking, and selected MCP integrations.

Usage:
    from openhands.tools.preset.default import default_tools

    tools = default_tools()

Notes:
- Presets are simple collections of tools and configuration, not a
  replacement for custom agents.
- They are stable entry points meant to reduce boilerplate for typical
  setups.
"""

from .default import get_default_agent
from .gemini import get_gemini_agent, get_gemini_tools
from .gpt5 import get_gpt5_agent
from .planning import get_planning_agent


__all__ = [
    "get_default_agent",
    "get_gemini_agent",
    "get_gemini_tools",
    "get_gpt5_agent",
    "get_planning_agent",
]
