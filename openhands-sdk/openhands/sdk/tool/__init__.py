from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openhands.sdk._lazy_imports import import_lazy_symbol, lazy_dir


if TYPE_CHECKING:
    from .builtins import BUILT_IN_TOOL_CLASSES, BUILT_IN_TOOLS, FinishTool, ThinkTool
    from .registry import list_registered_tools, register_tool, resolve_tool
    from .schema import Action, Observation
    from .spec import Tool
    from .tool import (
        DeclaredResources,
        ExecutableTool,
        ToolAnnotations,
        ToolDefinition,
        ToolExecutor,
    )


__all__ = [
    "DeclaredResources",
    "Tool",
    "ToolDefinition",
    "ToolAnnotations",
    "ToolExecutor",
    "ExecutableTool",
    "Action",
    "Observation",
    "FinishTool",
    "ThinkTool",
    "BUILT_IN_TOOLS",
    "BUILT_IN_TOOL_CLASSES",
    "register_tool",
    "resolve_tool",
    "list_registered_tools",
]

_LAZY_IMPORTS = {
    "DeclaredResources": (".tool", "DeclaredResources"),
    "Tool": (".spec", "Tool"),
    "ToolDefinition": (".tool", "ToolDefinition"),
    "ToolAnnotations": (".tool", "ToolAnnotations"),
    "ToolExecutor": (".tool", "ToolExecutor"),
    "ExecutableTool": (".tool", "ExecutableTool"),
    "Action": (".schema", "Action"),
    "Observation": (".schema", "Observation"),
    "FinishTool": (".builtins", "FinishTool"),
    "ThinkTool": (".builtins", "ThinkTool"),
    "BUILT_IN_TOOLS": (".builtins", "BUILT_IN_TOOLS"),
    "BUILT_IN_TOOL_CLASSES": (".builtins", "BUILT_IN_TOOL_CLASSES"),
    "register_tool": (".registry", "register_tool"),
    "resolve_tool": (".registry", "resolve_tool"),
    "list_registered_tools": (".registry", "list_registered_tools"),
}


def __getattr__(name: str) -> Any:
    return import_lazy_symbol(__name__, globals(), _LAZY_IMPORTS, name)


def __dir__() -> list[str]:
    return lazy_dir(globals(), __all__)
