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

from collections.abc import Sequence

from openhands.sdk.tool.spec import Tool


DEFAULT_EXEC_TOOL_NAMES: tuple[str, ...] = (
    "terminal",
    "file_editor",
    "task_tracker",
)
"""Names of the standard exec tools every default OpenHands agent gets."""

BROWSER_TOOL_NAME = "browser_tool_set"
"""Name of the browser tool set, added only when ``enable_browser`` is set."""

SUB_AGENT_TOOL_NAME = "task_tool_set"
"""Name of the sub-agent delegation tool set."""

SWITCH_LLM_TOOL_NAME = "switch_llm"
"""Name of the built-in LLM-switching tool."""


def resolve_tool_specs(
    tools: Sequence[Tool] | None,
    *,
    enable_browser: bool = False,
    enable_switch_llm: bool = True,
) -> list[Tool]:
    """Resolve ``tools`` to specs: ``None`` is the standard set, a list is kept."""
    if tools is not None:
        return list(tools)
    resolved = _preset_specs(enable_browser=enable_browser)
    if enable_switch_llm:
        resolved.append(Tool(name=SWITCH_LLM_TOOL_NAME))
    return resolved


def canonical_tool_name(name: str) -> str:
    """Return the tool name a spec resolves to, collapsing built-in class names."""
    from openhands.sdk.tool.builtins import BUILT_IN_TOOL_CLASSES

    tool_class = BUILT_IN_TOOL_CLASSES.get(name)
    return tool_class.name if tool_class is not None else name


def _preset_specs(*, enable_browser: bool) -> list[Tool]:
    specs = [Tool(name=name) for name in DEFAULT_EXEC_TOOL_NAMES]
    if enable_browser:
        specs.append(Tool(name=BROWSER_TOOL_NAME))
    return specs


def default_tool_specs(
    *,
    enable_sub_agents: bool = False,
    enable_browser: bool = False,
) -> list[Tool]:
    """Default tool specs for an OpenHands agent whose settings carry no tools.

    ``enable_sub_agents`` is retained for the legacy ``agent_settings`` path,
    where the switch still exists; agent profiles select the sub-agent tool set
    in ``tools`` instead.

    Deterministic: the same inputs yield the same specs on every runtime.
    Browser is off by default (see :data:`BROWSER_TOOL_NAME` — the serving
    layer enables it where it can actually run).
    """
    specs = _preset_specs(enable_browser=enable_browser)
    if enable_sub_agents:
        specs.append(Tool(name=SUB_AGENT_TOOL_NAME))
    return specs
