"""Reject dynamic attribute access in SDK source files."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


FORBIDDEN = {"getattr", "setattr"}


def violations(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        (node.lineno, node.func.id)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in FORBIDDEN
    ]


def main(paths: list[str]) -> int:
    found = False
    for raw_path in paths:
        path = Path(raw_path)
        for line, name in violations(path):
            print(f"{path}:{line}: forbidden dynamic attribute call: {name}")
            found = True
    return int(found)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
