"""Implementing essential tools that doesn't interact with the environment.

These are built in and are *required* for the agent to work.

For tools that require interacting with the environment, add them to `openhands-tools`.
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
from openhands.sdk.tool.builtins.think import (
    ThinkAction,
    ThinkExecutor,
    ThinkObservation,
    ThinkTool,
)


# Tools attached to every agent by default. `InvokeSkillTool` is deliberately
# *not* here: it's auto-attached by `Agent._initialize` only when an
# AgentSkills-format skill is loaded (see BUILT_IN_TOOL_CLASSES below).
BUILT_IN_TOOLS = [FinishTool, ThinkTool]

# Map of built-in tool class names to their classes. Includes
# `InvokeSkillTool` so it can be resolved by name from `include_default_tools`
# and the conditional wiring in `Agent._initialize`.
BUILT_IN_TOOL_CLASSES = {
    **{tool.__name__: tool for tool in BUILT_IN_TOOLS},
    InvokeSkillTool.__name__: InvokeSkillTool,
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
    "ThinkTool",
    "ThinkAction",
    "ThinkObservation",
    "ThinkExecutor",
]
