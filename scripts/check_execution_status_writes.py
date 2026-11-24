"""Reject direct writes to ``execution_status`` in package source.

``ConversationState.transition()`` is the only sanctioned way to change a
conversation's execution status: it validates the move against the status
table and then assigns the field. This hook flags every other assignment to an
``execution_status`` attribute (``x.execution_status = ...``, annotated or
augmented) and every ``setattr(x, "execution_status", ...)``.

Assignments to objects that only mirror the status (a server-side index record,
an outgoing webhook payload) are not state transitions. Mark such a write with
a ``# status-write: <why>`` comment, either on the line where the statement
starts or on its own line directly above it.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path


ATTRIBUTE = "execution_status"
PRAGMA = "# status-write:"
PACKAGE_ROOTS = (
    "openhands-sdk",
    "openhands-agent-server",
    "openhands-tools",
    "openhands-workspace",
)


def _is_status_target(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == ATTRIBUTE


def _is_status_setattr(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Name)
        and node.func.id == "setattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == ATTRIBUTE
    )


def _is_marked(lines: list[str], lineno: int) -> bool:
    above = lines[lineno - 2].strip() if lineno > 1 else ""
    return PRAGMA in lines[lineno - 1] or above.startswith(PRAGMA)


def violations(path: Path) -> list[int]:
    """Return the line numbers of unmarked ``execution_status`` writes."""
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(path))
    found: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            hit = any(_is_status_target(target) for target in node.targets)
        elif isinstance(node, ast.AnnAssign | ast.AugAssign):
            hit = _is_status_target(node.target)
        elif isinstance(node, ast.Call):
            hit = _is_status_setattr(node)
        else:
            continue
        if hit and not _is_marked(lines, node.lineno):
            found.append(node.lineno)
    return sorted(found)


def _discover_files() -> list[Path]:
    root = Path(__file__).resolve().parent.parent
    return [
        path
        for package in PACKAGE_ROOTS
        for path in sorted((root / package).glob("**/*.py"))
    ]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths", nargs="*", help="Python files to check (default: all packages)"
    )
    args = parser.parse_args(argv)
    paths = [Path(p) for p in args.paths] if args.paths else _discover_files()

    failed = False
    for path in paths:
        for line in violations(path):
            failed = True
            print(
                f"{path}:{line}: assign execution_status through "
                "ConversationState.transition(), or mark a mirror-only write "
                f"with '{PRAGMA} <why>'"
            )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
