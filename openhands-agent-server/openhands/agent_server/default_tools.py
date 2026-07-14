from collections.abc import Sequence

from openhands.sdk.tool import (
    BROWSER_TOOL_NAME,
    Tool,
    default_tool_specs,
    is_tool_usable,
)


def resolve_default_tools(
    extra_tool_names: Sequence[str] = (),
    *,
    enable_sub_agents: bool = False,
) -> list[Tool]:
    tools = default_tool_specs(enable_sub_agents=enable_sub_agents)
    names = {tool.name for tool in tools}

    for name in (BROWSER_TOOL_NAME, *extra_tool_names):
        if name not in names and is_tool_usable(name):
            tools.append(Tool(name=name))
            names.add(name)

    return tools
