"""Canonical default tool names for the standard OpenHands agent.

Tool *names* are a wire contract: they are persisted in settings/profile JSON
and sent by clients, independently of where the implementations live. Keeping
the canonical defaults here lets ``openhands-sdk`` (which must not import
``openhands-tools``) default a toolset from data alone — ``Tool`` is a spec
(name + params) resolved to an implementation only at runtime via the registry.

``openhands.tools.preset.default.get_default_tools`` remains the constructor
that also registers the implementations; ``tests/cross`` asserts it stays in
lockstep with these names.
"""

from openhands.sdk.tool.spec import Tool


DEFAULT_EXEC_TOOL_NAMES: tuple[str, ...] = (
    "terminal",
    "file_editor",
    "task_tracker",
)
"""Names of the standard exec tools every default OpenHands agent gets."""

BROWSER_TOOL_NAME = "browser_tool_set"
"""Name of the browser tool set.

Not part of the deterministic default: browser is an environment-dependent
capability. Its implementation declares it as a runtime default, and serving
layers add it only where the Chromium stack is usable.
"""

SUB_AGENT_TOOL_NAME = "task_tool_set"
"""Name of the sub-agent delegation tool set, gated on ``enable_sub_agents``."""


def default_tool_specs(
    *,
    enable_sub_agents: bool = False,
    enable_browser: bool = False,
) -> list[Tool]:
    """Default tool specs for an OpenHands agent whose settings carry no tools.

    Deterministic: the same inputs yield the same specs on every runtime.
    Browser is off by default (see :data:`BROWSER_TOOL_NAME` — the serving
    layer injects it where it can actually run); pass ``enable_browser=True``
    to include it explicitly.
    """
    names = list(DEFAULT_EXEC_TOOL_NAMES)
    if enable_browser:
        names.append(BROWSER_TOOL_NAME)
    if enable_sub_agents:
        names.append(SUB_AGENT_TOOL_NAME)
    return [Tool(name=name) for name in names]
