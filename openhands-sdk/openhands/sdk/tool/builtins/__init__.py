"""SDK-resident tools that do not interact with the environment.

`BUILT_IN_TOOLS` contains tools attached to every agent. `BUILT_IN_TOOL_CLASSES`
also includes optional SDK tools that are resolved by name from agent setup.

Tools that require interacting with the environment belong in `openhands-tools`.
"""

from openhands.sdk.tool.builtins.ask_user import (
    ASK_USER_TOOL_NAME,
    AskUserAction,
    AskUserExecutor,
    AskUserObservation,
    AskUserTool,
)
from openhands.sdk.tool.builtins.classify_and_switch_llm import (
    ClassifyAndSwitchLLMAction,
    ClassifyAndSwitchLLMExecutor,
    ClassifyAndSwitchLLMObservation,
    ClassifyAndSwitchLLMTool,
)
from openhands.sdk.tool.builtins.finish import (
    FinishAction,
    FinishExecutor,
    FinishObservation,
    FinishTool,
)
from openhands.sdk.tool.builtins.invoke_skill import (
    InvokeSkillAction,
    InvokeSkillExecutor,
    InvokeSkillObservation,
    InvokeSkillTool,
)
from openhands.sdk.tool.builtins.switch_llm import (
    SwitchLLMAction,
    SwitchLLMExecutor,
    SwitchLLMObservation,
    SwitchLLMTool,
)
from openhands.sdk.tool.builtins.think import (
    ThinkAction,
    ThinkExecutor,
    ThinkObservation,
    ThinkTool,
)
from openhands.sdk.tool.builtins.vision_inspect import (
    VisionInspectAction,
    VisionInspectExecutor,
    VisionInspectObservation,
    VisionInspectTool,
)
from openhands.sdk.tool.tool import ToolDefinition


# Tools attached to every agent by default. `InvokeSkillTool` is deliberately
# *not* here: it's auto-attached by `Agent._initialize` only when an
# AgentSkills-format skill is loaded (see BUILT_IN_TOOL_CLASSES below).
BUILT_IN_TOOLS = [FinishTool, ThinkTool]

# Map of built-in tool class names to their classes. Includes optional built-ins
# so they can be resolved by name from `include_default_tools` and the
# conditional wiring in `Agent._initialize`.
BUILT_IN_TOOL_CLASSES = {
    **{tool.__name__: tool for tool in BUILT_IN_TOOLS},
    InvokeSkillTool.__name__: InvokeSkillTool,
    SwitchLLMTool.__name__: SwitchLLMTool,
    VisionInspectTool.__name__: VisionInspectTool,
    ClassifyAndSwitchLLMTool.__name__: ClassifyAndSwitchLLMTool,
    AskUserTool.__name__: AskUserTool,
}

BUILT_IN_TOOLS_WITH_PARAMS = frozenset({ClassifyAndSwitchLLMTool.__name__})


def builtin_tool_class(name: str) -> type[ToolDefinition] | None:
    """Return the built-in tool class named by its class name or its tool name."""
    if name in BUILT_IN_TOOL_CLASSES:
        return BUILT_IN_TOOL_CLASSES[name]
    return next(
        (tool for tool in BUILT_IN_TOOL_CLASSES.values() if tool.name == name), None
    )


__all__ = [
    "BUILT_IN_TOOLS",
    "BUILT_IN_TOOL_CLASSES",
    "BUILT_IN_TOOLS_WITH_PARAMS",
    "builtin_tool_class",
    "ASK_USER_TOOL_NAME",
    "AskUserTool",
    "AskUserAction",
    "AskUserObservation",
    "AskUserExecutor",
    "ClassifyAndSwitchLLMTool",
    "ClassifyAndSwitchLLMAction",
    "ClassifyAndSwitchLLMObservation",
    "ClassifyAndSwitchLLMExecutor",
    "FinishTool",
    "FinishAction",
    "FinishObservation",
    "FinishExecutor",
    "InvokeSkillTool",
    "InvokeSkillAction",
    "InvokeSkillObservation",
    "InvokeSkillExecutor",
    "SwitchLLMTool",
    "SwitchLLMAction",
    "SwitchLLMObservation",
    "SwitchLLMExecutor",
    "ThinkTool",
    "ThinkAction",
    "ThinkObservation",
    "ThinkExecutor",
    "VisionInspectTool",
    "VisionInspectAction",
    "VisionInspectObservation",
    "VisionInspectExecutor",
]
