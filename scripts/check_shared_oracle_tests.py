#!/usr/bin/env python3
"""
Flag tests whose expected value comes from a call the code under test also makes.

Example (from #5703):

    supported = get_supported_openai_params(model=model) or []
    expected = "reasoning_effort" in supported
    assert get_features(model).supports_reasoning_effort == expected

``get_features`` itself calls ``get_supported_openai_params``, so both sides
of the assertion move together: the test passes whatever the dependency
returns and cannot catch a wrong answer. Pin a literal expected value instead.

A call is reported when:
1. an ``assert`` compares two sides (==, !=, is, is not);
2. one side calls a function from ``openhands.*`` (the code under test);
3. the other side, directly or through local variables, calls a function that
   the code under test also calls (followed through ``openhands.*`` helpers);
4. that shared function belongs to a third-party package: not ``openhands``,
   not a builtin or the standard library, and not a class.

Add ``# shared-oracle: <reason>`` on the assert line when sharing is intended.
"""

import ast
import sys
from functools import cache
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
SOURCE_ROOTS = [
    REPO_ROOT / "openhands-sdk",
    REPO_ROOT / "openhands-tools",
    REPO_ROOT / "openhands-agent-server",
    REPO_ROOT / "openhands-workspace",
]
SUPPRESS = "# shared-oracle:"
FOLLOW_DEPTH = 4
IGNORED_ROOTS = set(sys.stdlib_module_names) | {
    "builtins",
    "pytest",
    "typing_extensions",
}
COMPARISONS = (ast.Eq, ast.NotEq, ast.Is, ast.IsNot)


def _module_file(module: str) -> Path | None:
    relative = Path(*module.split("."))
    for root in SOURCE_ROOTS:
        for candidate in (
            root / relative.with_suffix(".py"),
            root / relative / "__init__.py",
        ):
            if candidate.exists():
                return candidate
    return None


def _imports(tree: ast.Module, module: str) -> dict[str, str]:
    """Local name -> qualified name, for imports anywhere in the module."""
    names: dict[str, str] = {}
    package = module if module.endswith("__init__") else module.rsplit(".", 1)[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                base = ".".join(
                    parts[: len(parts) - node.level + 1] + ([base] if base else [])
                )
            for alias in node.names:
                names[alias.asname or alias.name] = f"{base}.{alias.name}"
    return names


@cache
def _parse_module(module: str) -> tuple[dict[str, str], dict[str, ast.AST]] | None:
    """Imports and function definitions (``f``, ``Class.f``) of an openhands module."""
    path = _module_file(module)
    if path is None:
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None
    functions: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            functions[node.name] = node
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions[f"{node.name}.{item.name}"] = item
    return _imports(tree, module), functions


def _definition(qualified: str, depth: int = 0) -> tuple[str, ast.AST] | None:
    """Find where an openhands name is defined, following re-exports."""
    if depth > 5:
        return None
    module, _, name = qualified.rpartition(".")
    parsed = _parse_module(module)
    if parsed is None:
        # ``pkg.mod.Class.method``: the module is one level further up.
        module2, _, cls = module.rpartition(".")
        parsed2 = _parse_module(module2)
        if parsed2 and f"{cls}.{name}" in parsed2[1]:
            return module2, parsed2[1][f"{cls}.{name}"]
        return None
    imports, functions = parsed
    if name in functions:
        return module, functions[name]
    if name in imports and imports[name].startswith("openhands."):
        return _definition(imports[name], depth + 1)
    return None


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _resolve(
    name: str, imports: dict[str, str], local_functions: set[str], module: str
) -> str | None:
    head, _, rest = name.partition(".")
    if head in imports:
        return f"{imports[head]}.{rest}" if rest else imports[head]
    if head in local_functions:
        return f"{module}.{name}"
    return None


def _called_names(node: ast.AST) -> list[str]:
    names = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and (name := _dotted(sub.func)):
            names.append(name)
    return names


@cache
def _reachable_calls(qualified: str) -> frozenset[str]:
    """Qualified names an openhands function calls, following openhands helpers."""
    seen: set[str] = set()
    found: set[str] = set()
    frontier = [(qualified, 0)]
    while frontier:
        current, depth = frontier.pop()
        if current in seen or depth > FOLLOW_DEPTH:
            continue
        seen.add(current)
        definition = _definition(current)
        if definition is None:
            continue
        module, node = definition
        imports, functions = _parse_module(module)  # type: ignore[misc]
        top_level = {n.split(".")[0] for n in functions}
        for name in _called_names(node):
            resolved = _resolve(name, imports, top_level, module)
            if resolved is None and name.startswith("self."):
                owner = next(
                    (
                        n.split(".")[0]
                        for n, f in functions.items()
                        if f is node and "." in n
                    ),
                    None,
                )
                resolved = f"{module}.{owner}.{name[5:]}" if owner else None
            if resolved is None:
                continue
            found.add(resolved)
            if resolved.startswith("openhands."):
                frontier.append((resolved, depth + 1))
    return frozenset(found)


def _is_oracle_candidate(qualified: str) -> bool:
    """A third-party function: the dependency whose answer the code under test trusts.

    Expected values built with openhands helpers are left alone: tests that check
    two SDK parts agree (wiring, parity, round trips) do that on purpose.
    """
    root = qualified.split(".")[0]
    last = qualified.rsplit(".", 1)[-1]
    return (
        root not in IGNORED_ROOTS
        and root != "openhands"
        and not last[:1].isupper()
        and not last.startswith("_")
    )


class _TestChecker:
    def __init__(self, path: Path, tree: ast.Module, lines: list[str]):
        self.path = path
        self.lines = lines
        self.imports = _imports(tree, "tests")
        self.errors: list[str] = []

    def check_function(self, func: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        assigned: dict[str, list[ast.AST]] = {}
        for node in ast.walk(func):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for target in targets:
                    for leaf in ast.walk(target):
                        if isinstance(leaf, ast.Name):
                            assigned.setdefault(leaf.id, []).append(node.value)
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Assert)
                and isinstance(node.test, ast.Compare)
                and len(node.test.ops) == 1
                and isinstance(node.test.ops[0], COMPARISONS)
            ):
                self._check_assert(
                    node, node.test.left, node.test.comparators[0], assigned
                )

    def _calls(
        self, expr: ast.AST, assigned: dict[str, list[ast.AST]], depth: int = 0
    ) -> set[str]:
        calls = {
            q
            for name in _called_names(expr)
            if (q := _resolve(name, self.imports, set(), "tests"))
        }
        if depth < 3:
            for sub in ast.walk(expr):
                if isinstance(sub, ast.Name) and sub.id in assigned:
                    for value in assigned[sub.id]:
                        calls |= self._calls(value, assigned, depth + 1)
        return calls

    def _check_assert(
        self,
        node: ast.Assert,
        left: ast.AST,
        right: ast.AST,
        assigned: dict[str, list[ast.AST]],
    ) -> None:
        source = "\n".join(
            self.lines[node.lineno - 1 : (node.end_lineno or node.lineno)]
        )
        if SUPPRESS in source:
            return
        sides = [self._calls(left, assigned), self._calls(right, assigned)]
        for subject_side, oracle_side in ((0, 1), (1, 0)):
            subjects = {c for c in sides[subject_side] if c.startswith("openhands.")}
            oracles = {
                c for c in sides[oracle_side] if _is_oracle_candidate(c)
            } - subjects
            for subject in sorted(subjects):
                shared = sorted(oracles & _reachable_calls(subject))
                if shared:
                    rel = (
                        self.path.relative_to(REPO_ROOT)
                        if self.path.is_relative_to(REPO_ROOT)
                        else self.path
                    )
                    self.errors.append(
                        f"{rel}:{node.lineno}: the expected value comes from "
                        f"{', '.join(shared)}, which {subject} also calls, so the "
                        "assertion cannot fail when that call is wrong"
                    )
                    return


def check_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
    except (OSError, SyntaxError) as e:
        return [f"{path}: could not parse: {e}"]
    checker = _TestChecker(path, tree, text.splitlines())
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name.startswith("test"):
            checker.check_function(node)
    return checker.errors


def main(files: list[str] | None = None) -> int:
    tests_root = REPO_ROOT / "tests"
    if files:
        paths = [Path(f).resolve() for f in files]
        paths = [
            p
            for p in paths
            if p.is_relative_to(tests_root) and p.name.startswith("test_")
        ]
    else:
        paths = sorted(tests_root.rglob("test_*.py"))
    errors = [error for path in paths for error in check_file(path)]
    for error in errors:
        print(f"[ERROR] {error}")
    if errors:
        print(
            "\nPin a literal expected value instead of recomputing it with the "
            "code under test's own dependency.\nIf the shared call is intended, add "
            f"'{SUPPRESS} <reason>' on the assert line."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or None))
