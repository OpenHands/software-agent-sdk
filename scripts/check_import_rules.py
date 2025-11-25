#!/usr/bin/env python3
"""
Check import dependency rules across openhands packages.

Rules:
1. openhands.sdk should NOT import from:
   - openhands.tools
   - openhands.workspace
   - openhands.agent_server

2. openhands.tools can import from:
   - openhands.sdk ✓
   BUT NOT from:
   - openhands.workspace
   - openhands.agent_server

3. openhands.workspace can import from:
   - openhands.sdk ✓
   - openhands.tools ✓
   BUT NOT from:
   - openhands.agent_server

4. openhands.agent_server can import from:
   - openhands.sdk ✓
   - openhands.tools ✓
   BUT NOT from:
   - openhands.workspace
"""

import ast
import sys
from pathlib import Path


class ImportChecker(ast.NodeVisitor):
    """AST visitor to extract import statements."""

    def __init__(self):
        self.imports: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.add(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.add(node.module)
        self.generic_visit(node)


def get_imports_from_file(file_path: Path) -> set[str]:
    """Extract all import module names from a Python file."""
    try:
        with open(file_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(file_path))
        checker = ImportChecker()
        checker.visit(tree)
        return checker.imports
    except SyntaxError as e:
        print(f"Warning: Could not parse {file_path}: {e}", file=sys.stderr)
        return set()
    except Exception as e:
        print(f"Warning: Error reading {file_path}: {e}", file=sys.stderr)
        return set()


def check_sdk_imports(sdk_path: Path) -> list[tuple[Path, str]]:
    """Check that openhands.sdk doesn't import from tools/workspace/agent_server."""  # noqa: E501
    violations = []
    forbidden = ["openhands.tools", "openhands.workspace", "openhands.agent_server"]

    for py_file in sdk_path.rglob("*.py"):
        imports = get_imports_from_file(py_file)
        for imp in imports:
            for forbidden_module in forbidden:
                if imp == forbidden_module or imp.startswith(f"{forbidden_module}."):
                    violations.append((py_file, imp))

    return violations


def check_tools_imports(tools_path: Path) -> list[tuple[Path, str]]:
    """Check that openhands.tools doesn't import from workspace or agent_server."""
    violations = []
    forbidden = ["openhands.workspace", "openhands.agent_server"]

    for py_file in tools_path.rglob("*.py"):
        imports = get_imports_from_file(py_file)
        for imp in imports:
            for forbidden_module in forbidden:
                if imp == forbidden_module or imp.startswith(f"{forbidden_module}."):
                    violations.append((py_file, imp))

    return violations


def check_agent_server_imports(agent_server_path: Path) -> list[tuple[Path, str]]:
    """Check that openhands.agent_server doesn't import from workspace."""
    violations = []
    forbidden = ["openhands.workspace"]

    for py_file in agent_server_path.rglob("*.py"):
        imports = get_imports_from_file(py_file)
        for imp in imports:
            for forbidden_module in forbidden:
                if imp == forbidden_module or imp.startswith(f"{forbidden_module}."):
                    violations.append((py_file, imp))

    return violations


def main(files: list[str] | None = None) -> int:
    """
    Main entry point for import rule checking.

    Args:
        files: Optional list of specific files to check. If None, checks all files.

    Returns:
        0 if no violations found, 1 otherwise.
    """
    repo_root = Path(__file__).parent.parent
    sdk_path = repo_root / "openhands-sdk" / "openhands" / "sdk"
    tools_path = repo_root / "openhands-tools" / "openhands" / "tools"
    agent_server_path = (
        repo_root / "openhands-agent-server" / "openhands" / "agent_server"
    )

    # If specific files are provided, filter checks to only those directories
    if files:
        # Convert file paths to absolute for comparison
        abs_files = [str(Path(f).resolve()) for f in files]
        check_sdk = any(str(sdk_path) in f for f in abs_files)
        check_tools = any(str(tools_path) in f for f in abs_files)
        check_agent_server = any(str(agent_server_path) in f for f in abs_files)
    else:
        # Check all packages if no files specified
        check_sdk = True
        check_tools = True
        check_agent_server = True

    all_violations = []

    # Check SDK imports
    if check_sdk and sdk_path.exists():
        violations = check_sdk_imports(sdk_path)
        if violations:
            print("❌ Violations in openhands.sdk:")
            for file, imp in violations:
                rel_path = file.relative_to(repo_root)
                print(
                    f"  {rel_path}: imports {imp} "
                    "(sdk should not import tools/workspace/agent_server)"
                )
            all_violations.extend(violations)

    # Check tools imports
    if check_tools and tools_path.exists():
        violations = check_tools_imports(tools_path)
        if violations:
            print("❌ Violations in openhands.tools:")
            for file, imp in violations:
                rel_path = file.relative_to(repo_root)
                print(
                    f"  {rel_path}: imports {imp} "
                    "(tools should not import workspace/agent_server)"
                )
            all_violations.extend(violations)

    # Check agent_server imports
    if check_agent_server and agent_server_path.exists():
        violations = check_agent_server_imports(agent_server_path)
        if violations:
            print("❌ Violations in openhands.agent_server:")
            for file, imp in violations:
                rel_path = file.relative_to(repo_root)
                print(
                    f"  {rel_path}: imports {imp} "
                    "(agent_server should not import workspace)"
                )
            all_violations.extend(violations)

    if all_violations:
        print(
            "\n💡 Import dependency rules:\n"
            "  - openhands.sdk: Cannot import tools/workspace/agent_server\n"
            "  - openhands.tools: Cannot import workspace/agent_server "
            "(can import sdk)\n"
            "  - openhands.agent_server: Cannot import workspace "
            "(can import sdk/tools)\n"
            "  - openhands.workspace: Can import sdk/tools"
        )
        return 1

    print("✅ All import dependency rules satisfied!")
    return 0


if __name__ == "__main__":
    # Get files from command line arguments (from pre-commit)
    files = sys.argv[1:] if len(sys.argv) > 1 else None
    sys.exit(main(files))
