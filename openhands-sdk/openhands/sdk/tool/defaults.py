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
"""Name of the browser tool set, included in the default when the runtime
has it registered and usable (chromium available)."""

SUB_AGENT_TOOL_NAME = "task_tool_set"
"""Name of the sub-agent delegation tool set, gated on ``enable_sub_agents``."""


def default_tool_specs(
    *,
    enable_sub_agents: bool = False,
    enable_browser: bool | None = None,
) -> list[Tool]:
    """Default tool specs for an OpenHands agent whose settings carry no tools.

    ``enable_browser=None`` (the default) is adaptive: browser tools are
    included exactly when the current runtime has them registered and usable
    (chromium present) — the same bar canvas applies via ``GET /tools`` on the
    settings launch path — so a default-toolset agent gets browser wherever it
    can actually run, and resolving the spec never raises on a runtime without
    it. Pass True/False to force.
    """
    names = list(DEFAULT_EXEC_TOOL_NAMES)
    if enable_browser is None:
        # Local import: the registry is populated by openhands-tools at import
        # time in the serving process; consulted lazily so this module stays
        # import-light and cycle-free.
        from openhands.sdk.tool.registry import is_tool_usable

        enable_browser = is_tool_usable(BROWSER_TOOL_NAME)
    if enable_browser:
        names.append(BROWSER_TOOL_NAME)
    if enable_sub_agents:
        names.append(SUB_AGENT_TOOL_NAME)
    return [Tool(name=name) for name in names]
