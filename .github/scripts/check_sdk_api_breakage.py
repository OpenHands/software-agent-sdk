#!/usr/bin/env python3
"""API breakage detection for published OpenHands packages using Griffe.

This script compares current workspace packages against their previous PyPI
releases to detect breaking changes in the public API.

It focuses on the curated public surface:
- symbols exported via ``__all__``
- public members removed from classes exported via ``__all__``

It enforces two policies:

1. **Deprecation-before-removal** – any removed export or removed public class
   member must have been marked deprecated in the *previous* release using the
   canonical deprecation helpers (``@deprecated`` decorator or
   ``warn_deprecated()`` call from ``openhands.sdk.utils.deprecation``). For
   members, the recommended ``warn_deprecated`` feature name is qualified (e.g.
   ``"LLM.some_method"``).

2. **MINOR version bump** – any breaking change (removal or structural) requires
   at least a MINOR version bump according to SemVer.

Complementary to the deprecation mechanism:
- Deprecation (``check_deprecations.py``): enforces cleanup deadlines
- This script: prevents unannounced removals and enforces SemVer bumps
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import tomllib
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from packaging import version as pkg_version


@dataclass(frozen=True)
class PackageConfig:
    """Configuration for a single published package."""

    package: str  # dotted module path, e.g. "openhands.sdk"
    distribution: str  # PyPI distribution name, e.g. "openhands-sdk"
    source_dir: str  # repo-relative directory, e.g. "openhands-sdk"


@dataclass(frozen=True, slots=True)
class DeprecatedSymbols:
    """Deprecated SDK symbols detected in a source tree.

    ``top_level`` tracks module-level symbols (exports) like ``LLM``.
    ``qualified`` tracks class members like ``LLM.some_method``.
    """

    top_level: set[str] = frozenset()  # type: ignore[assignment]
    qualified: set[str] = frozenset()  # type: ignore[assignment]


PACKAGES: tuple[PackageConfig, ...] = (
    PackageConfig(
        package="openhands.sdk",
        distribution="openhands-sdk",
        source_dir="openhands-sdk",
    ),
    PackageConfig(
        package="openhands.workspace",
        distribution="openhands-workspace",
        source_dir="openhands-workspace",
    ),
    PackageConfig(
        package="openhands.tools",
        distribution="openhands-tools",
        source_dir="openhands-tools",
    ),
)


def read_version_from_pyproject(path: str) -> str:
    """Read the version string from a pyproject.toml file."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    proj = data.get("project", {})
    v = proj.get("version")
    if not v:
        raise SystemExit(f"Could not read version from {path}")
    return str(v)


def _parse_version(v: str) -> pkg_version.Version:
    """Parse a version string using packaging."""
    return pkg_version.parse(v)


def get_prev_pypi_version(pkg: str, current: str | None) -> str | None:
    """Fetch the previous release version from PyPI.

    Args:
        pkg: Package name on PyPI (e.g., "openhands-sdk")
        current: Current version to find the predecessor of, or None for latest

    Returns:
        Previous version string, or None if not found or on network error
    """
    req = urllib.request.Request(
        url=f"https://pypi.org/pypi/{pkg}/json",
        headers={"User-Agent": "openhands-sdk-api-check/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            meta = json.load(r)
    except Exception as e:
        print(f"::warning title={pkg} API::Failed to fetch PyPI metadata: {e}")
        return None

    releases = list(meta.get("releases", {}).keys())
    if not releases:
        return None

    def _sort_key(s: str):
        return _parse_version(s)

    if current is None:
        releases_sorted = sorted(releases, key=_sort_key, reverse=True)
        return releases_sorted[0]

    cur_parsed = _parse_version(current)
    older = [rv for rv in releases if _parse_version(rv) < cur_parsed]
    if not older:
        return None
    return sorted(older, key=_sort_key, reverse=True)[0]


def ensure_griffe() -> None:
    """Verify griffe is installed, raising an error if not."""
    try:
        import griffe  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "ERROR: griffe not installed. Install with: pip install griffe[pypi]\n"
        )
        raise SystemExit(1)


def _is_field_metadata_only_change(old_val: object, new_val: object) -> bool:
    """Check if the change is only in Field metadata (description, title, etc.).

    Field metadata parameters like ``description``, ``title``, and ``examples``
    don't affect runtime behavior - they're documentation-only. Changes to these
    should not be considered breaking API changes.

    Returns:
        True if both values are Field() calls and only metadata parameters differ.
    """
    old_str = str(old_val)
    new_str = str(new_val)

    if not (old_str.startswith("Field(") and new_str.startswith("Field(")):
        return False

    # Metadata parameters that don't affect runtime behavior
    # See https://docs.pydantic.dev/latest/api/fields/#pydantic.fields.Field
    metadata_params = ["description", "title", "examples", "json_schema_extra"]

    old_normalized = old_str
    new_normalized = new_str

    for param in metadata_params:
        # Pattern to match param='...' or param="..." with simple string values
        pattern = rf'{param}\s*=\s*([\'"])([^\'"]*?)\1'
        old_normalized = re.sub(pattern, f"{param}=PLACEHOLDER", old_normalized)
        new_normalized = re.sub(pattern, f"{param}=PLACEHOLDER", new_normalized)

    return old_normalized == new_normalized


def _collect_breakages_pairs(
    objs: Iterable[tuple[object, object]],
    *,
    deprecated: DeprecatedSymbols,
    title: str,
) -> tuple[list[object], int]:
    """Find breaking changes between pairs of old/new API objects.

    Only reports breakages for public API members.

    Returns:
        (breakages, undeprecated_removals)
    """

    import griffe
    from griffe import Alias, AliasResolutionError, BreakageKind, ExplanationStyle, Kind

    breakages: list[object] = []
    undeprecated_removals = 0

    for old, new in objs:
        try:
            for br in griffe.find_breaking_changes(old, new):
                obj = getattr(br, "obj", None)
                if not getattr(obj, "is_public", True):
                    continue

                # Skip ATTRIBUTE_CHANGED_VALUE when it's just Field metadata changes
                # (description, title, examples, etc.) - these don't affect runtime
                if br.kind == BreakageKind.ATTRIBUTE_CHANGED_VALUE:
                    old_value = getattr(br, "old_value", None)
                    new_value = getattr(br, "new_value", None)
                    if _is_field_metadata_only_change(old_value, new_value):
                        print(
                            f"::notice title={title}::Ignoring Field metadata-only "
                            f"change (non-breaking): {obj.name if obj else 'unknown'}"
                        )
                        continue

                print(br.explain(style=ExplanationStyle.GITHUB))
                breakages.append(br)

                if br.kind != BreakageKind.OBJECT_REMOVED:
                    continue

                parent = getattr(obj, "parent", None)
                if getattr(parent, "kind", None) != Kind.CLASS:
                    continue

                feature = f"{parent.name}.{obj.name}"
                if (
                    feature not in deprecated.qualified
                    and parent.name not in deprecated.top_level
                ):
                    print(
                        f"::error title={title}::Removed '{feature}' without prior "
                        "deprecation. Mark it with @deprecated(...) or "
                        f"warn_deprecated('{feature}', ...) for at least one release "
                        "before removing."
                    )
                    undeprecated_removals += 1
        except AliasResolutionError as e:
            if isinstance(old, Alias) or isinstance(new, Alias):
                old_target = old.target_path if isinstance(old, Alias) else None
                new_target = new.target_path if isinstance(new, Alias) else None
                if old_target != new_target:
                    name = getattr(old, "name", None) or getattr(
                        new, "name", "<unknown>"
                    )
                    print(
                        f"::warning title={title}::Alias target changed for '{name}': "
                        f"{old_target!r} -> {new_target!r}"
                    )
                    breakages.append(
                        {
                            "kind": "ALIAS_TARGET_CHANGED",
                            "name": name,
                            "old": old_target,
                            "new": new_target,
                        }
                    )
            else:
                print(
                    f"::notice title={title}::Skipping symbol comparison due to "
                    f"unresolved alias: {e}"
                )
        except Exception as e:
            print(f"::warning title={title}::Failed to compute breakages: {e}")

    return breakages, undeprecated_removals


def _extract_exported_names(module) -> set[str]:
    """Extract names exported from a module via ``__all__``.

    This check is explicitly meant to track the curated public surface. The SDK
    is expected to define ``__all__`` in ``openhands.sdk``; if it's missing or we
    can't statically interpret it, we fail fast rather than silently widening the
    surface area (which would make the check noisy and brittle).
    """
    try:
        all_var = module["__all__"]
    except Exception as e:
        raise ValueError("Expected __all__ to be defined on the public module") from e

    val = getattr(all_var, "value", None)
    elts = getattr(val, "elements", None)
    if not elts:
        raise ValueError("Unable to statically evaluate __all__")

    names: set[str] = set()
    for el in elts:
        # Griffe represents string literals in __all__ in different ways depending
        # on how the module is loaded / griffe version:
        # - sometimes as plain Python strings (including quotes, e.g. "'LLM'")
        # - sometimes as expression nodes with a `.value` attribute
        #
        # We intentionally only support the "static __all__ of string literals"
        # case; we just normalize the representation.
        if isinstance(el, str):
            names.add(el.strip("\"'"))
            continue
        s = getattr(el, "value", None)
        if isinstance(s, str):
            names.add(s)

    if not names:
        raise ValueError("__all__ resolved to an empty set")

    return names


def _check_version_bump(prev: str, new_version: str, total_breaks: int) -> int:
    """Check if version bump policy is satisfied for breaking changes.

    Policy: Breaking changes require at least a MINOR version bump.

    Returns:
        0 if policy satisfied, 1 if not
    """
    if total_breaks == 0:
        print("No breaking changes detected")
        return 0

    parsed_prev = _parse_version(prev)
    parsed_new = _parse_version(new_version)

    # MINOR bump required: same major, higher minor OR higher major
    ok = (parsed_new.major > parsed_prev.major) or (
        parsed_new.major == parsed_prev.major and parsed_new.minor > parsed_prev.minor
    )

    if not ok:
        print(
            f"::error title=SemVer::Breaking changes detected ({total_breaks}); "
            f"require at least minor version bump from "
            f"{parsed_prev.major}.{parsed_prev.minor}.x, but new is {new_version}"
        )
        return 1

    print(
        f"Breaking changes detected ({total_breaks}) and version bump policy "
        f"satisfied ({prev} -> {new_version})"
    )
    return 0


def _resolve_griffe_object(
    root: object,
    dotted: str,
    root_package: str = "",
) -> object:
    """Resolve a dotted path to a griffe object."""
    root_path = getattr(root, "path", None)
    if root_path == dotted:
        return root

    if isinstance(root_path, str) and dotted.startswith(root_path + "."):
        dotted = dotted[len(root_path) + 1 :]

    try:
        return root[dotted]
    except (KeyError, TypeError) as e:
        print(
            f"::warning title=SDK API::Unable to resolve {dotted} via "
            f"direct lookup; falling back to manual traversal: {e}"
        )

    rel = dotted
    if root_package and dotted.startswith(root_package + "."):
        rel = dotted[len(root_package) + 1 :]

    obj = root
    for part in rel.split("."):
        try:
            obj = obj[part]
        except (KeyError, TypeError) as e:
            raise KeyError(f"Unable to resolve {dotted}: failed at {part}") from e
    return obj


def _load_current(
    griffe_module: object, repo_root: str, cfg: PackageConfig
) -> object | None:
    try:
        return griffe_module.load(
            cfg.package,
            search_paths=[os.path.join(repo_root, cfg.source_dir)],
        )
    except Exception as e:
        print(
            f"::error title={cfg.distribution} API::"
            f"Failed to load current {cfg.distribution}: {e}"
        )
        return None


def _load_prev_from_pypi(
    griffe_module: object,
    prev: str,
    cfg: PackageConfig,
) -> object | None:
    griffe_cache = os.path.expanduser("~/.cache/griffe")
    os.makedirs(griffe_cache, exist_ok=True)

    try:
        return griffe_module.load_pypi(
            package=cfg.package,
            distribution=cfg.distribution,
            version_spec=f"=={prev}",
        )
    except Exception as e:
        print(
            f"::error title={cfg.distribution} API::"
            f"Failed to load {cfg.distribution}=={prev} from PyPI: {e}"
        )
        return None


def _find_deprecated_symbols(source_root: Path) -> DeprecatedSymbols:
    """Scan source files for symbols marked with the SDK deprecation helpers.

    Detects two forms:
    - ``@deprecated(...)`` decorator on a class/function/method
    - ``warn_deprecated('SomeFeature', ...)`` call

    Returns:
        DeprecatedSymbols(top_level=..., qualified=...)
    """

    def _is_deprecated_decorator(deco: ast.AST) -> bool:
        if not isinstance(deco, ast.Call):
            return False
        target = deco.func
        if isinstance(target, ast.Name):
            return target.id == "deprecated"
        if isinstance(target, ast.Attribute):
            return target.attr == "deprecated"
        return False

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.class_stack: list[str] = []
            self.top_level: set[str] = set()
            self.qualified: set[str] = set()

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
            if any(_is_deprecated_decorator(deco) for deco in node.decorator_list):
                self.top_level.add(node.name)
                self.qualified.add(node.name)

            self.class_stack.append(node.name)
            self.generic_visit(node)
            self.class_stack.pop()

        def _visit_function_like(
            self,
            node: ast.FunctionDef | ast.AsyncFunctionDef,
        ) -> None:
            if any(_is_deprecated_decorator(deco) for deco in node.decorator_list):
                if self.class_stack:
                    self.qualified.add(".".join([*self.class_stack, node.name]))
                else:
                    self.top_level.add(node.name)
                    self.qualified.add(node.name)

            self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            self._visit_function_like(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            self._visit_function_like(node)

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            target = node.func
            func_name = None
            if isinstance(target, ast.Name):
                func_name = target.id
            elif isinstance(target, ast.Attribute):
                func_name = target.attr

            if func_name == "warn_deprecated" and node.args:
                feature = _extract_string_literal(node.args[0])
                if feature is not None:
                    self.qualified.add(feature)
                    self.top_level.add(feature.split(".")[0])

            self.generic_visit(node)

    top_level: set[str] = set()
    qualified: set[str] = set()

    for pyfile in source_root.rglob("*.py"):
        try:
            tree = ast.parse(pyfile.read_text())
        except SyntaxError as e:
            print(
                f"::warning title=SDK API::Skipping {pyfile}: "
                f"failed to parse (SyntaxError: {e})"
            )
            continue

        visitor = _Visitor()
        visitor.visit(tree)
        top_level |= visitor.top_level
        qualified |= visitor.qualified

    return DeprecatedSymbols(top_level=top_level, qualified=qualified)


def _extract_string_literal(node: ast.AST) -> str | None:
    """Return the string value if *node* is a simple string literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _get_source_root(griffe_root: object) -> Path | None:
    """Derive the package source directory from a griffe module's filepath."""
    filepath = getattr(griffe_root, "filepath", None)
    if filepath is not None:
        return Path(filepath).parent
    return None


def _compute_breakages(old_root, new_root, cfg: PackageConfig) -> tuple[int, int]:
    """Detect breaking changes between old and new package versions.

    Returns:
        ``(total_breaks, undeprecated_removals)`` — *total_breaks* counts all
        structural breakages (for the version-bump policy), while
        *undeprecated_removals* counts public API removals (exports and class
        members) without a prior deprecation marker (a separate hard failure).
    """
    pkg = cfg.package
    title = f"{cfg.distribution} API"
    total_breaks = 0
    undeprecated_removals = 0

    source_root = _get_source_root(old_root)
    deprecated = (
        _find_deprecated_symbols(source_root) if source_root else DeprecatedSymbols()
    )

    try:
        old_mod = _resolve_griffe_object(old_root, pkg, root_package=pkg)
        new_mod = _resolve_griffe_object(new_root, pkg, root_package=pkg)
    except Exception as e:
        raise RuntimeError(f"Failed to resolve root module '{pkg}'") from e

    new_exports = _extract_exported_names(new_mod)
    try:
        old_exports = _extract_exported_names(old_mod)
    except ValueError as e:
        # The API breakage check relies on a curated public surface defined via
        # __all__. If the previous release didn't define (or couldn't statically
        # evaluate) __all__, we can't compute meaningful breakages.
        #
        # In this situation, skip rather than failing the entire workflow.
        print(
            f"::notice title={title}::Skipping breakage check; previous release "
            f"has no statically-evaluable {pkg}.__all__: {e}"
        )
        return 0, 0

    removed = sorted(old_exports - new_exports)

    # Check deprecation-before-removal policy (exports)
    for name in removed:
        total_breaks += 1  # every removal is a structural break
        if name not in deprecated.top_level:
            print(
                f"::error title={title}::Removed '{name}' from "
                f"{pkg}.__all__ without prior deprecation. "
                "Mark it with @deprecated or warn_deprecated() "
                "for at least one release before removing."
            )
            undeprecated_removals += 1
        else:
            print(
                f"::notice title={title}::Removed previously-deprecated symbol "
                f"'{name}' from {pkg}.__all__"
            )

    common = sorted(old_exports & new_exports)
    pairs: list[tuple[object, object]] = []
    for name in common:
        try:
            pairs.append((old_mod[name], new_mod[name]))
        except Exception as e:
            print(f"::warning title={title}::Unable to resolve symbol {name}: {e}")

    breakages, undeprecated_members = _collect_breakages_pairs(
        pairs,
        deprecated=deprecated,
        title=title,
    )
    total_breaks += len(breakages)
    undeprecated_removals += undeprecated_members

    return total_breaks, undeprecated_removals


def _check_package(griffe_module, repo_root: str, cfg: PackageConfig) -> int:
    """Run breakage checks for a single package. Returns 0 on success."""
    pyproj = os.path.join(repo_root, cfg.source_dir, "pyproject.toml")
    new_version = read_version_from_pyproject(pyproj)

    title = f"{cfg.distribution} API"
    prev = get_prev_pypi_version(cfg.distribution, new_version)
    if not prev:
        print(
            f"::warning title={title}::No previous {cfg.distribution} "
            f"release found; skipping breakage check",
        )
        return 0

    print(f"Comparing {cfg.distribution} {new_version} against {prev}")

    new_root = _load_current(griffe_module, repo_root, cfg)
    if not new_root:
        return 1

    old_root = _load_prev_from_pypi(griffe_module, prev, cfg)
    if not old_root:
        return 1

    try:
        total_breaks, undeprecated = _compute_breakages(old_root, new_root, cfg)
    except Exception as e:
        print(f"::error title={title}::Failed to compute breakages: {e}")
        return 1

    if undeprecated:
        print(
            f"::error title={title}::{undeprecated} symbol(s) removed "
            f"from {cfg.package} without prior deprecation — "
            f"see errors above"
        )

    bump_rc = _check_version_bump(prev, new_version, total_breaks)

    return 1 if (undeprecated or bump_rc) else 0


def main() -> int:
    """Main entry point for API breakage detection."""
    ensure_griffe()
    import griffe

    repo_root = os.getcwd()
    rc = 0
    for cfg in PACKAGES:
        print(f"\n{'=' * 60}")
        print(f"Checking {cfg.distribution} ({cfg.package})")
        print(f"{'=' * 60}")
        rc |= _check_package(griffe, repo_root, cfg)

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
