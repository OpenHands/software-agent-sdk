"""Gemini-style file editing tools.

This module provides gemini-style file editing tools as an alternative to
the claude-style file_editor tool. These tools are designed to match the
tool interface used by gemini-cli.

Tools:
    - read_file: Read file content with pagination support
    - write_file: Full file overwrite operations
    - edit: Find and replace with validation
    - list_directory: Directory listing with metadata

Usage:
    To use gemini-style tools instead of the standard FileEditorTool,
    replace FileEditorTool with the four gemini tools:

    ```python
    from openhands.tools.gemini import GEMINI_FILE_TOOLS

    agent = Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name),
            *GEMINI_FILE_TOOLS,  # Instead of Tool(name=FileEditorTool.name)
        ],
    )
    ```

    Or individually:

    ```python
    from openhands.tools.gemini import (
        ReadFileTool, WriteFileTool, EditTool, ListDirectoryTool
    )

    agent = Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name=ReadFileTool.name),
            Tool(name=WriteFileTool.name),
            Tool(name=EditTool.name),
            Tool(name=ListDirectoryTool.name),
        ],
    )
    ```
"""

from openhands.sdk import Tool
from openhands.tools.gemini.edit import EditAction, EditObservation, EditTool
from openhands.tools.gemini.list_directory import (
    ListDirectoryAction,
    ListDirectoryObservation,
    ListDirectoryTool,
)
from openhands.tools.gemini.read_file import (
    ReadFileAction,
    ReadFileObservation,
    ReadFileTool,
)
from openhands.tools.gemini.write_file import (
    WriteFileAction,
    WriteFileObservation,
    WriteFileTool,
)


# Convenience list for easy replacement of FileEditorTool
GEMINI_FILE_TOOLS: list[Tool] = [
    Tool(name=ReadFileTool.name),
    Tool(name=WriteFileTool.name),
    Tool(name=EditTool.name),
    Tool(name=ListDirectoryTool.name),
]

__all__ = [
    # Convenience list
    "GEMINI_FILE_TOOLS",
    # Individual tools
    "ReadFileTool",
    "ReadFileAction",
    "ReadFileObservation",
    "WriteFileTool",
    "WriteFileAction",
    "WriteFileObservation",
    "EditTool",
    "EditAction",
    "EditObservation",
    "ListDirectoryTool",
    "ListDirectoryAction",
    "ListDirectoryObservation",
]
