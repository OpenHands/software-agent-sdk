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

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from openhands.sdk.tool.spec import Tool
from openhands.sdk.utils.deprecation import warn_deprecated


if TYPE_CHECKING:
    from openhands.sdk.tool.tool import ToolDefinition


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
) -> list[Tool]:
    """Resolve ``tools`` to specs: ``None`` is the standard set, a list is kept."""
    if tools is not None:
        return list(tools)
    return [
        *_preset_specs(enable_browser=enable_browser),
        Tool(name=SWITCH_LLM_TOOL_NAME),
    ]


def launch_tool_specs(
    tools: Sequence[Tool] | None, *, browser_available: bool
) -> list[Tool]:
    """Resolve ``tools`` for a runtime, leaving out a browser it cannot run."""
    resolved = resolve_tool_specs(tools, enable_browser=browser_available)
    if browser_available:
        return resolved
    return [tool for tool in resolved if tool.name != BROWSER_TOOL_NAME]


def effective_builtin_class(name: str) -> "type[ToolDefinition] | None":
    """Return the built-in ``name`` resolves to, unless another tool holds it."""
    from openhands.sdk.tool.builtins import builtin_tool_class
    from openhands.sdk.tool.registry import registered_tool_class

    builtin = builtin_tool_class(name)
    if builtin is None or registered_tool_class(name) not in (None, builtin):
        return None
    return builtin


def canonical_tool_name(name: str) -> str:
    """Return the tool name a spec resolves to, collapsing built-in class names."""
    builtin = effective_builtin_class(name)
    if builtin is None or effective_builtin_class(builtin.name) is not builtin:
        return name
    return builtin.name


def selects_tool(tools: Sequence[Tool], name: str) -> bool:
    return any(canonical_tool_name(tool.name) == name for tool in tools)


def merge_duplicate_tools(tools: Sequence[Tool]) -> list[Tool]:
    """Collapse specs that name the same tool, keeping the first one with params."""
    by_name: dict[str, Tool] = {}
    for tool in tools:
        name = canonical_tool_name(tool.name)
        kept = by_name.get(name)
        if kept is None or (tool.params and not kept.params):
            by_name[name] = Tool(name=name, params=tool.params)
    return list(by_name.values())


def reject_builtin_params(tools: Sequence[Tool]) -> None:
    """Raise if a built-in that takes no parameters is given some."""
    from openhands.sdk.tool.builtins import BUILT_IN_TOOLS_WITH_PARAMS

    for tool in tools:
        tool_class = effective_builtin_class(tool.name)
        if (
            set(tool.params) - {"response_schema"}
            and tool_class is not None
            and tool_class.__name__ not in BUILT_IN_TOOLS_WITH_PARAMS
        ):
            raise ValueError(f"Tool {tool.name!r} does not accept parameters")


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

    Deterministic: the same inputs yield the same specs on every runtime.
    Browser is off by default (see :data:`BROWSER_TOOL_NAME` — the serving
    layer enables it where it can actually run).
    """
    specs = _preset_specs(enable_browser=enable_browser)
    if enable_sub_agents:
        specs.append(Tool(name=SUB_AGENT_TOOL_NAME))
    return specs


RETIRED_TOOL_SWITCHES = ("enable_sub_agents", "enable_switch_llm_tool")
_BOOL_ADAPTER = TypeAdapter(bool)


def drop_retired_tool_switches(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k not in RETIRED_TOOL_SWITCHES}


def fold_deprecated_tool_switches(
    data: Any, *, owner: str, enable_browser: bool
) -> Any:
    """Fold deprecated switch input into ``tools`` and warn."""
    if not isinstance(data, Mapping) or not any(
        key in data for key in RETIRED_TOOL_SWITCHES
    ):
        return data
    warn_deprecated(
        f"{owner}.enable_sub_agents and {owner}.enable_switch_llm_tool",
        deprecated_in="1.51.0",
        removed_in="1.56.0",
        details="Select task_tool_set and switch_llm in `tools` instead.",
    )
    return fold_retired_tool_switches(data, sparse=True, enable_browser=enable_browser)


def fold_retired_tool_switches(
    payload: Mapping[str, Any],
    *,
    sparse: bool = False,
    enable_browser: bool = True,
) -> dict[str, Any]:
    """Fold the retired switches into ``tools``; ``sparse`` skips absent ones."""
    folded = dict(payload)
    sub_agents = _pop_switch(folded, "enable_sub_agents", None if sparse else False)
    switch_llm = _pop_switch(folded, "enable_switch_llm_tool", None if sparse else True)
    tools = folded.get("tools")
    if tools is not None and not isinstance(tools, list | tuple):
        return folded
    # Persisted `[]` predates `None` as the default.
    if not sparse and tools == []:
        tools = folded["tools"] = None
    if tools is None and not sub_agents and switch_llm is not False:
        return folded
    # "The standard set plus/minus one tool" is not expressible, so pin it.
    if tools is None:
        entries = _preset_specs(enable_browser=enable_browser)
        if sub_agents:
            entries.append(Tool(name=SUB_AGENT_TOOL_NAME))
        if switch_llm is None:
            switch_llm = True
    else:
        entries = [t if isinstance(t, Tool) else Tool.model_validate(t) for t in tools]
        # A stored switch only ever fed the default set.
        if sparse and sub_agents is not None:
            entries = _toggle(entries, SUB_AGENT_TOOL_NAME, sub_agents)
    if switch_llm is not None:
        entries = _toggle(entries, SWITCH_LLM_TOOL_NAME, switch_llm)
    folded["tools"] = entries
    return folded


def _toggle(entries: list[Tool], name: str, enabled: bool) -> list[Tool]:
    if not enabled:
        return [e for e in entries if canonical_tool_name(e.name) != name]
    if not selects_tool(entries, name):
        return [*entries, Tool(name=name)]
    return entries


def _pop_switch(payload: dict[str, Any], key: str, default: bool | None) -> bool | None:
    if key not in payload:
        return default
    return _BOOL_ADAPTER.validate_python(payload.pop(key))
