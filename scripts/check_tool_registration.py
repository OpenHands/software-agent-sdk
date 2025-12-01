#!/usr/bin/env python3
"""
Check that all Tool subclasses are automatically registered on import.

Rules:
1. All ToolDefinition subclasses should call register_tool() at module level
2. The register_tool() call should be at the end of the module
3. Registration should use the pattern: register_tool(ToolName.name, ToolName)
"""

import ast
import sys
from pathlib import Path


class ToolChecker(ast.NodeVisitor):
    """AST visitor to check Tool registration."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.tool_classes: set[str] = set()
        self.registered_tools: set[str] = set()
        self.imports_register_tool = False

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Check if register_tool is imported."""
        if node.module and "openhands.sdk.tool" in node.module:
            for alias in node.names:
                if alias.name == "register_tool":
                    self.imports_register_tool = True
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Find all ToolDefinition subclasses."""
        # Check if this class inherits from ToolDefinition
        for base in node.bases:
            base_name = self._get_name(base)
            # Check for direct inheritance or generic inheritance
            if "ToolDefinition" in base_name:
                self.tool_classes.add(node.name)
                break
        self.generic_visit(node)

    def visit_Expr(self, node: ast.Expr) -> None:
        """Find register_tool() calls."""
        if isinstance(node.value, ast.Call):
            func = node.value
            if isinstance(func.func, ast.Name) and func.func.id == "register_tool":
                # Check if the second argument is a tool class name
                if len(func.args) >= 2:
                    tool_arg = func.args[1]
                    if isinstance(tool_arg, ast.Name):
                        self.registered_tools.add(tool_arg.id)
        self.generic_visit(node)

    def _get_name(self, node: ast.expr) -> str:
        """Extract name from an AST node (handles Name, Attribute, Subscript)."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{self._get_name(node.value)}.{node.attr}"
        elif isinstance(node, ast.Subscript):
            return self._get_name(node.value)
        return ""


def check_tool_registration(
    file_path: Path, is_special_file: bool = False
) -> list[str]:
    """Check that all Tool subclasses in a file are registered.

    Args:
        file_path: Path to the Python file to check
        is_special_file: If True, only checks that at least one tool is registered
                        (for files with toolset patterns)

    Returns:
        List of error messages (empty if no issues found)
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(file_path))
    except SyntaxError as e:
        return [f"Syntax error: {e}"]
    except Exception as e:
        return [f"Error reading file: {e}"]

    checker = ToolChecker(file_path)
    checker.visit(tree)

    errors = []

    # Check if file defines any Tool classes
    if not checker.tool_classes:
        return []  # No tools defined, nothing to check

    # For special files (like browser_use), just check that SOME tool is registered
    if is_special_file:
        if checker.tool_classes and not checker.registered_tools:
            errors.append(
                "File defines Tool classes but none are registered. "
                "At least one tool should be registered."
            )
        return errors

    # Check if register_tool is imported when tools are defined
    if checker.tool_classes and not checker.imports_register_tool:
        errors.append(
            "File defines Tool classes but does not import register_tool "
            "from openhands.sdk.tool"
        )

    # Check that all defined tools are registered
    unregistered = checker.tool_classes - checker.registered_tools
    if unregistered:
        for tool in sorted(unregistered):
            errors.append(
                f"Tool '{tool}' is defined but not registered. "
                f"Add: register_tool({tool}.name, {tool})"
            )

    return errors


def main(files: list[str] | None = None) -> int:
    """
    Main entry point for tool registration checking.

    Args:
        files: Optional list of specific files to check. If None, checks all files.

    Returns:
        0 if no violations found, 1 otherwise.
    """
    repo_root = Path(__file__).parent.parent
    tools_path = repo_root / "openhands-tools" / "openhands" / "tools"

    # Skip checking certain files/directories
    skip_patterns = {
        "__init__.py",
        "preset",  # Preset modules don't define tools, just use them
        "impl.py",  # Implementation files for executors
        "executor.py",  # Executor files
    }

    # Files with special patterns (e.g., toolsets that register one tool for many)
    # These files are checked manually to ensure at least one tool is registered
    special_files = {
        "browser_use/definition.py",  # Registers BrowserToolSet for all browser tools
        "delegate/definition.py",  # May have special registration patterns
    }

    if files:
        # Filter to only check files in the tools directory
        files_to_check = [
            Path(f).resolve()
            for f in files
            if str(tools_path) in str(Path(f).resolve())
            and Path(f).name.endswith(".py")
        ]
    else:
        # Check all Python files in tools directory
        files_to_check = list(tools_path.rglob("*.py"))

    # Filter out files matching skip patterns
    files_to_check = [
        f
        for f in files_to_check
        if not any(pattern in str(f) for pattern in skip_patterns)
    ]

    all_errors = []

    for file_path in files_to_check:
        # Check if this is a special file
        rel_path = file_path.relative_to(repo_root)
        is_special = any(special in str(rel_path) for special in special_files)

        errors = check_tool_registration(file_path, is_special_file=is_special)
        if errors:
            print(f"❌ Tool registration issues in {rel_path}:")
            for error in errors:
                print(f"  {error}")
            all_errors.extend(errors)

    if all_errors:
        print(
            "\n💡 Tool registration rules:\n"
            "  - All ToolDefinition subclasses must be registered using "
            "register_tool()\n"
            "  - Add at module level: register_tool(ToolName.name, ToolName)\n"
            "  - Import register_tool from openhands.sdk.tool"
        )
        return 1

    print("✅ All Tool subclasses are properly registered!")
    return 0


if __name__ == "__main__":
    # Get files from command line arguments (from pre-commit)
    files = sys.argv[1:] if len(sys.argv) > 1 else None
    sys.exit(main(files))
