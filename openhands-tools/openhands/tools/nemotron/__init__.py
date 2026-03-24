"""Nemotron-compatible tools (Anthropic bash/str_replace schema).

Nemotron-3 Super (nvidia/nemotron-3-super-120b-a12b) was fine-tuned on
trajectories that use the Anthropic str_replace_based_edit_tool / bash
tool schema. This module exposes those exact tool names so the model's
calls succeed without any prompt engineering or model-side changes.

  bash        → BashExecutor        (model calls "bash", not "terminal")
  str_replace → StrReplaceExecutor  (model calls "str_replace", not "file_editor")

Tools:
    - bash: Run shell commands (Anthropic-compatible)
    - str_replace: File viewing and editing operations (Anthropic-compatible)

Usage:
    To use Nemotron-compatible tools instead of the standard terminal/file_editor:

    ```python
    from openhands.tools.nemotron import NEMOTRON_TOOLS

    agent = Agent(
        llm=llm,
        tools=[
            *NEMOTRON_TOOLS,
            Tool(name=TaskTrackerTool.name),
        ],
    )
    ```

    Or use the preset:

    ```python
    from openhands.tools.preset.nemotron import get_nemotron_agent

    agent = get_nemotron_agent(llm=llm)
    ```
"""

from openhands.sdk import Tool
from openhands.tools.nemotron.bash import (
    BashAction,
    BashExecutor,
    BashObservation,
    BashTool,
)
from openhands.tools.nemotron.str_replace import (
    StrReplaceAction,
    StrReplaceExecutor,
    StrReplaceObservation,
    StrReplaceTool,
)


# Convenience list for easy replacement of terminal/file_editor tools
NEMOTRON_TOOLS: list[Tool] = [
    Tool(name=BashTool.name),
    Tool(name=StrReplaceTool.name),
]

__all__ = [
    # Convenience list
    "NEMOTRON_TOOLS",
    # Bash tool
    "BashTool",
    "BashAction",
    "BashObservation",
    "BashExecutor",
    # StrReplace tool
    "StrReplaceTool",
    "StrReplaceAction",
    "StrReplaceObservation",
    "StrReplaceExecutor",
]
