#!/usr/bin/env python3
"""Validate docstrings conform to MDX-compatible formatting guidelines.

This script checks that docstrings in the SDK use patterns that render correctly
in Mintlify MDX documentation. It validates:

1. No REPL-style examples (>>>) - should use fenced code blocks instead
2. Shell/config examples use fenced code blocks (prevents # becoming headers)

Run with: python scripts/check_docstrings.py
Exit code 0 = all checks pass, 1 = violations found
"""

import ast
import sys
from dataclasses import dataclass
from pathlib import Path


# Directories to check
SDK_PATHS = [
    "openhands-sdk/openhands/sdk",
]

# Files/directories to skip
SKIP_PATTERNS = [
    "__pycache__",
    ".pyc",
    "test_",
    "_test.py",
]

# Core public API files to check strictly (these are documented on the website)
# Other files will be checked but only emit warnings, not failures
STRICT_CHECK_FILES = [
    "agent/agent.py",
    "llm/llm.py",
    "conversation/conversation.py",
    "tool/tool.py",
    "workspace/base.py",
    "observability/laminar.py",
]


@dataclass
class Violation:
    """A docstring formatting violation."""

    file: Path
    line: int
    name: str
    rule: str
    message: str
    is_strict: bool = False  # True if this is in a strictly-checked file


def should_skip(path: Path) -> bool:
    """Check if a path should be skipped."""
    path_str = str(path)
    return any(pattern in path_str for pattern in SKIP_PATTERNS)


def check_repl_examples(
    docstring: str, name: str, lineno: int, file: Path
) -> list[Violation]:
    """Check for REPL-style examples (>>>).

    These should be replaced with fenced code blocks for better MDX rendering.
    """
    violations = []
    lines = docstring.split("\n")

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(">>>"):
            violations.append(
                Violation(
                    file=file,
                    line=lineno + i,
                    name=name,
                    rule="no-repl-examples",
                    message=(
                        "Use fenced code blocks (```python) instead of >>> REPL style. "
                        "REPL examples don't render well in MDX documentation."
                    ),
                )
            )
            # Only report once per docstring
            break

    return violations


def check_unfenced_shell_config(
    docstring: str, name: str, lineno: int, file: Path
) -> list[Violation]:
    """Check for shell/config examples that aren't in fenced code blocks.

    Lines starting with # outside code blocks become markdown headers.
    """
    violations = []
    lines = docstring.split("\n")
    in_code_block = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Track code block state
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue

        # Skip if inside a code block
        if in_code_block:
            continue

        # Check for shell-style comments that look like config
        # Pattern: line starts with # and previous line has = (config pattern)
        if stripped.startswith("#") and not stripped.startswith("# "):
            # This is likely a shell comment without space (less common in prose)
            continue

        # Check for unfenced config: KEY=VALUE followed by # comment
        if i > 0:
            prev_line = lines[i - 1].strip() if i > 0 else ""
            # If previous line looks like config (VAR=value) and this is a # comment
            if "=" in prev_line and prev_line.split("=")[0].isupper():
                if stripped.startswith("# "):
                    violations.append(
                        Violation(
                            file=file,
                            line=lineno + i,
                            name=name,
                            rule="fenced-shell-config",
                            message=(
                                "Shell/config examples with # comments should be "
                                "in ```bash code blocks. Otherwise # becomes a "
                                "markdown header."
                            ),
                        )
                    )
                    # Only report once per docstring
                    break

    return violations


def check_docstring(
    docstring: str, name: str, lineno: int, file: Path
) -> list[Violation]:
    """Run all checks on a docstring."""
    if not docstring:
        return []

    violations = []
    violations.extend(check_repl_examples(docstring, name, lineno, file))
    violations.extend(check_unfenced_shell_config(docstring, name, lineno, file))
    return violations


def get_docstrings_from_file(file: Path) -> list[tuple[str, str, int]]:
    """Extract all docstrings from a Python file.

    Returns list of (name, docstring, lineno) tuples.
    """
    try:
        source = file.read_text()
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError) as e:
        print(f"Warning: Could not parse {file}: {e}", file=sys.stderr)
        return []

    docstrings = []

    for node in ast.walk(tree):
        name = None
        lineno = 0
        docstring = None

        if isinstance(node, ast.Module):
            docstring = ast.get_docstring(node)
            name = file.stem
            lineno = 1
        elif isinstance(node, ast.ClassDef):
            docstring = ast.get_docstring(node)
            name = node.name
            lineno = node.lineno
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            docstring = ast.get_docstring(node)
            name = node.name
            lineno = node.lineno

        if docstring and name:
            docstrings.append((name, docstring, lineno))

    return docstrings


def is_strict_file(file: Path, repo_root: Path) -> bool:
    """Check if a file is in the strict check list."""
    try:
        rel_path = file.relative_to(repo_root / "openhands-sdk/openhands/sdk")
        return any(str(rel_path) == strict for strict in STRICT_CHECK_FILES)
    except ValueError:
        return False


def check_file(file: Path, repo_root: Path) -> list[Violation]:
    """Check all docstrings in a file."""
    violations = []
    is_strict = is_strict_file(file, repo_root)

    for name, docstring, lineno in get_docstrings_from_file(file):
        file_violations = check_docstring(docstring, name, lineno, file)
        for v in file_violations:
            v.is_strict = is_strict
        violations.extend(file_violations)

    return violations


def main() -> int:
    """Run docstring checks on all SDK files."""
    repo_root = Path(__file__).parent.parent.parent

    all_violations: list[Violation] = []
    files_checked = 0

    for sdk_path in SDK_PATHS:
        path = repo_root / sdk_path
        if not path.exists():
            print(f"Warning: Path not found: {path}", file=sys.stderr)
            continue

        for py_file in path.rglob("*.py"):
            if should_skip(py_file):
                continue

            files_checked += 1
            violations = check_file(py_file, repo_root)
            all_violations.extend(violations)

    # Separate strict violations (errors) from warnings
    strict_violations = [v for v in all_violations if v.is_strict]
    warning_violations = [v for v in all_violations if not v.is_strict]

    # Report warnings (non-strict files)
    if warning_violations:
        count = len(warning_violations)
        print(f"\n⚠️  Found {count} docstring warning(s) in non-core files:\n")

        by_file: dict[Path, list[Violation]] = {}
        for v in warning_violations:
            by_file.setdefault(v.file, []).append(v)

        for file, violations in sorted(by_file.items()):
            rel_path = file.relative_to(repo_root)
            print(f"📄 {rel_path}")
            for v in violations:
                print(f"   Line {v.line}: {v.name} ({v.rule})")
        print()

    # Report errors (strict files)
    if strict_violations:
        count = len(strict_violations)
        print(f"\n❌ Found {count} docstring error(s) in core API files:\n")

        by_file: dict[Path, list[Violation]] = {}
        for v in strict_violations:
            by_file.setdefault(v.file, []).append(v)

        for file, violations in sorted(by_file.items()):
            rel_path = file.relative_to(repo_root)
            print(f"📄 {rel_path}")
            for v in violations:
                print(f"   Line {v.line}: {v.name}")
                print(f"   Rule: {v.rule}")
                print(f"   {v.message}")
                print()

        print("=" * 60)
        print("To fix these issues:")
        print("  1. Replace >>> examples with ```python code blocks")
        print("  2. Wrap shell/config examples in ```bash code blocks")
        print("=" * 60)
        return 1

    if warning_violations:
        count = len(warning_violations)
        print(f"✅ Core API files pass. {count} warnings in other files.")
    else:
        print(f"✅ All {files_checked} files pass docstring checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
