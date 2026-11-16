#!/usr/bin/env python3
"""Keep blocking store I/O off the agent-server event loop.

The persistence stores (settings, secrets, workspaces, LLM/agent/meta
profiles, provider connections, MCP OAuth tokens, runtime provisioning) are
synchronous: they read and write files under a file lock. Inside an ``async
def`` they must be reached through ``run_store`` from
``openhands.agent_server.persistence.async_store``, the one place where store
I/O hops to a worker thread.

This check flags, inside every ``async def`` under ``openhands-agent-server``:

1. a direct method call on a store (``store.load()``,
   ``get_settings_store(config).update(fn)``, ``registry.provisioning.save(x)``);
2. store work handed to any other thread hop (``asyncio.to_thread``,
   ``loop.run_in_executor``, ``run_in_threadpool``) instead of ``run_store``:
   a store method as the hopped callable, or a lambda that calls one.

A store expression is:

- a local name annotated with a store type or assigned from a store getter or
  constructor, or a getter call used inline;
- an attribute (``self.x`` or ``obj.x``) whose name some agent-server class
  declares with a store type or assigns from a store getter or constructor.
  Attribute names are matched across classes, so give a store attribute a
  name no non-store attribute uses.

Code in nested sync ``def``s and lambdas is not checked: those run wherever
their caller puts them, normally inside ``run_store``.

Limits: a store held in a container or reached through an unannotated
parameter is not seen, and a sync helper that does store I/O and is called
directly from async code is not followed. Methods in ``_PURE_METHODS`` touch
no file and are allowed.

Usage: check_async_store_access.py [FILES...]
(no files: scan openhands-agent-server)
"""

import ast
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCAN_ROOT = ROOT / "openhands-agent-server" / "openhands" / "agent_server"

STORE_TYPES = frozenset(
    {
        "SettingsStore",
        "FileSettingsStore",
        "SecretsStore",
        "FileSecretsStore",
        "WorkspacesStore",
        "FileWorkspacesStore",
        "LLMProfileStore",
        "AgentProfileStore",
        "MetaProfileStore",
        "ProviderConnectionStore",
        "MCPSettingsOAuthTokenStore",
        "RuntimeProvisioningStore",
    }
)

STORE_GETTERS = frozenset(
    {
        "get_settings_store",
        "get_secrets_store",
        "get_workspaces_store",
        "get_provider_connections_store",
        "get_llm_profile_store",
        "get_agent_profile_store",
    }
)

# Methods that only build or inspect an object and touch no file.
_PURE_METHODS = frozenset({"with_cipher", "manifest_path"})

THREAD_HOPS = frozenset({"to_thread", "run_in_executor", "run_in_threadpool"})
FACADE = "run_store"

_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    message: str

    def __str__(self) -> str:
        try:
            shown = self.path.relative_to(ROOT)
        except ValueError:
            shown = self.path
        return f"{shown}:{self.line}: {self.message}"


def _annotation_names(node: ast.expr | None) -> Iterator[str]:
    """Every bare or dotted type name mentioned in an annotation."""
    if node is None:
        return
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            yield sub.id
        elif isinstance(sub, ast.Attribute):
            yield sub.attr
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            try:
                parsed = ast.parse(sub.value, mode="eval").body
            except SyntaxError:
                continue
            yield from _annotation_names(parsed)


def _is_store_annotation(node: ast.expr | None) -> bool:
    return any(name in STORE_TYPES for name in _annotation_names(node))


def _callee_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _makes_store(value: ast.expr | None) -> bool:
    """True for ``get_x_store(...)`` or ``XStore(...)`` (possibly awaited)."""
    if isinstance(value, ast.Await):
        value = value.value
    if not isinstance(value, ast.Call):
        return False
    name = _callee_name(value)
    return name in STORE_GETTERS or name in STORE_TYPES


def store_attributes(tree: ast.AST) -> set[str]:
    """Attribute names that classes in ``tree`` use to hold a store."""
    attrs: set[str] = set()
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for node in ast.walk(cls):
            if isinstance(node, ast.AnnAssign):
                target = node.target
                if isinstance(target, ast.Name | ast.Attribute) and (
                    _is_store_annotation(node.annotation) or _makes_store(node.value)
                ):
                    attrs.add(
                        target.id if isinstance(target, ast.Name) else target.attr
                    )
            elif isinstance(node, ast.Assign) and _makes_store(node.value):
                attrs.update(
                    target.attr
                    for target in node.targets
                    if isinstance(target, ast.Attribute)
                )
    return attrs


def _own_nodes(func: ast.AsyncFunctionDef) -> Iterator[ast.AST]:
    """Nodes of ``func``'s body, not descending into nested scopes."""
    stack: list[ast.AST] = [
        stmt for stmt in func.body if not isinstance(stmt, _NESTED_SCOPES)
    ]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(
            child
            for child in ast.iter_child_nodes(node)
            if not isinstance(child, _NESTED_SCOPES)
        )


class _AsyncFunctionChecker:
    def __init__(
        self, path: Path, func: ast.AsyncFunctionDef, store_attrs: set[str]
    ) -> None:
        self.path = path
        self.func = func
        self.store_attrs = store_attrs
        self.store_names = self._collect_store_names()

    def _collect_store_names(self) -> set[str]:
        names: set[str] = set()
        args = self.func.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            if _is_store_annotation(arg.annotation):
                names.add(arg.arg)
        for node in _own_nodes(self.func):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if _is_store_annotation(node.annotation) or _makes_store(node.value):
                    names.add(node.target.id)
            elif isinstance(node, ast.Assign) and _makes_store(node.value):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
        return names

    def _is_store(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.store_names
        if isinstance(node, ast.Attribute):
            return node.attr in self.store_attrs
        return _makes_store(node)

    def _store_method(self, node: ast.expr) -> str | None:
        """``m`` when ``node`` is ``<store>.m`` for a method that does I/O."""
        if (
            isinstance(node, ast.Attribute)
            and node.attr not in _PURE_METHODS
            and self._is_store(node.value)
        ):
            return node.attr
        return None

    def _hopped_store_work(self, call: ast.Call, callee: str) -> str | None:
        """The store method a thread hop runs, if its callable is store work.

        Only the callable position counts (``run_in_executor`` takes the
        executor first); a store method passed further along is a callback
        for the hopped function, not the hop's own work.
        """
        position = 1 if callee == "run_in_executor" else 0
        if len(call.args) <= position:
            return None
        hopped = call.args[position]
        if isinstance(hopped, ast.Lambda):
            for sub in ast.walk(hopped.body):
                if isinstance(sub, ast.Call):
                    method = self._store_method(sub.func)
                    if method is not None:
                        return method
            return None
        return self._store_method(hopped)

    def violations(self) -> Iterator[Violation]:
        for node in _own_nodes(self.func):
            if not isinstance(node, ast.Call):
                continue
            method = self._store_method(node.func)
            if method is not None:
                yield Violation(
                    self.path,
                    node.lineno,
                    f"blocking store call .{method}() in async "
                    f"{self.func.name}(); await {FACADE}(...) instead",
                )
                continue
            callee = _callee_name(node)
            if callee not in THREAD_HOPS:
                continue
            hopped = self._hopped_store_work(node, callee)
            if hopped is not None:
                yield Violation(
                    self.path,
                    node.lineno,
                    f"store work .{hopped} handed to {callee}() in "
                    f"async {self.func.name}(); use {FACADE}(...)",
                )


def check_source(
    path: Path, source: str, store_attrs: Iterable[str] = ()
) -> list[Violation]:
    """Violations in one module; ``store_attrs`` adds attribute names declared
    elsewhere (the module's own classes are always read)."""
    tree = ast.parse(source, filename=str(path))
    attrs = store_attributes(tree) | set(store_attrs)
    found = [
        violation
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        for violation in _AsyncFunctionChecker(path, node, attrs).violations()
    ]
    return sorted(found, key=lambda v: v.line)


def _targets(argv: list[str]) -> Iterable[Path]:
    if not argv:
        return sorted(SCAN_ROOT.rglob("*.py"))
    paths = [Path(arg).resolve() for arg in argv]
    return [p for p in paths if p.suffix == ".py" and p.is_relative_to(SCAN_ROOT)]


def main(argv: list[str]) -> int:
    # Store attributes are collected from the whole package even when only
    # some files are checked: ``registry.provisioning`` is declared elsewhere.
    sources = {
        path: path.read_text(encoding="utf-8") for path in SCAN_ROOT.rglob("*.py")
    }
    store_attrs: set[str] = set()
    for source in sources.values():
        store_attrs |= store_attributes(ast.parse(source))
    violations: list[Violation] = []
    for path in _targets(argv):
        source = sources.get(path) or path.read_text(encoding="utf-8")
        violations.extend(check_source(path, source, store_attrs))
    for violation in violations:
        print(violation)
    if violations:
        print(
            f"\n{len(violations)} blocking store access(es) in async code. "
            f"Wrap the store work in a sync function and await {FACADE}(fn, ...)."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
