from openhands.sdk.tool import (
    BROWSER_TOOL_NAME,
    Tool,
    default_tool_specs,
    is_tool_usable,
    list_usable_default_tools,
)


def resolve_default_tools(
    *,
    enable_sub_agents: bool = False,
) -> list[Tool]:
    tools = default_tool_specs(enable_sub_agents=enable_sub_agents)
    names = {tool.name for tool in tools}

    if BROWSER_TOOL_NAME not in names and is_tool_usable(BROWSER_TOOL_NAME):
        tools.append(Tool(name=BROWSER_TOOL_NAME))
        names.add(BROWSER_TOOL_NAME)

    for name in list_usable_default_tools():
        if name not in names:
            tools.append(Tool(name=name))
            names.add(name)

    return tools
