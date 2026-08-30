"""SDK-resident tools that do not interact with the environment.

`BUILT_IN_TOOLS` contains tools attached to every agent. `BUILT_IN_TOOL_CLASSES`
also includes optional SDK tools that are resolved by name from agent setup.

Tools that require interacting with the environment belong in `openhands-tools`.
"""

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
}

__all__ = [
    "BUILT_IN_TOOLS",
    "BUILT_IN_TOOL_CLASSES",
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
