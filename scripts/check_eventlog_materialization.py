#!/usr/bin/env python3
"""Check for materializing EventLog events with list()."""

import ast
import sys
from pathlib import Path


ERROR_CODE = "ELOG001"
ERROR_MESSAGE = "Avoid materializing events with list(); iterate or use iter_events()."


class EventListMaterializationChecker(ast.NodeVisitor):
    """AST visitor that detects list(events) materialization calls."""

    def __init__(self, file_path: Path, lines: list[str]) -> None:
        self.file_path = file_path
        self.lines = lines
        self.violations: list[tuple[int, int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_list_call(node) and self._is_events_expr(node.args[0]):
            if not self._is_noqa(node.lineno):
                self.violations.append((node.lineno, node.col_offset, ERROR_MESSAGE))
        self.generic_visit(node)

    @staticmethod
    def _is_list_call(node: ast.Call) -> bool:
        return (
            isinstance(node.func, ast.Name)
            and node.func.id == "list"
            and len(node.args) == 1
        )

    def _is_noqa(self, lineno: int) -> bool:
        if lineno <= 0 or lineno > len(self.lines):
            return False
        line = self.lines[lineno - 1]
        return "# noqa" in line and (ERROR_CODE in line or "ELOG" in line)

    def _is_events_expr(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id == "events"
        if isinstance(node, ast.Attribute):
            return node.attr == "events"
        if isinstance(node, ast.Subscript):
            return self._is_events_expr(node.value)
        return False


def _check_file(file_path: Path) -> list[tuple[int, int, str]]:
    try:
        contents = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"Warning: Could not read {file_path}: {exc}", file=sys.stderr)
        return []

    try:
        tree = ast.parse(contents, filename=str(file_path))
    except SyntaxError as exc:
        print(f"Warning: Could not parse {file_path}: {exc}", file=sys.stderr)
        return []

    checker = EventListMaterializationChecker(file_path, contents.splitlines())
    checker.visit(tree)
    return checker.violations


def main(files: list[str] | None = None) -> int:
    """Check provided files for EventLog materialization violations."""
    target_files = [Path(f) for f in files or []]
    violations: list[tuple[Path, int, int, str]] = []

    for file_path in target_files:
        if file_path.suffix != ".py":
            continue
        for line, col, message in _check_file(file_path):
            violations.append((file_path, line, col, message))

    if violations:
        for file_path, line, col, message in violations:
            print(
                f"{file_path}:{line}:{col + 1}: {ERROR_CODE} {message}",
                file=sys.stderr,
            )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
