#!/usr/bin/env python3
"""SDK API breakage detection using Griffe.

This script compares the current workspace SDK against the previous PyPI release
to detect breaking changes in the public API. It focuses on symbols exported via
`__all__` in `openhands.sdk` and enforces a MINOR version bump policy when
breaking changes are detected.

Complementary to the deprecation mechanism:
- Deprecation (`check_deprecations.py`): Handles planned lifecycle with user warnings
- This script: Catches unplanned/accidental API breaks automatically
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
import urllib.request
from collections.abc import Iterable


# Package configuration - centralized for maintainability
SDK_PACKAGE = "openhands.sdk"
DISTRIBUTION_NAME = "openhands-sdk"
PYPROJECT_RELATIVE_PATH = "openhands-sdk/pyproject.toml"


def read_version_from_pyproject(path: str) -> str:
    """Read the version string from a pyproject.toml file."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    proj = data.get("project", {})
    v = proj.get("version")
    if not v:
        raise SystemExit(f"Could not read version from {path}")
    return str(v)


def _version_tuple_fallback(v: str) -> tuple[int, int, int]:
    """Parse version string into (major, minor, patch) tuple.

    Handles versions like "1.2.3", "1.2.3a1", "1.2.3.dev0", etc.
    """
    parts = v.split(".")
    nums: list[int] = []
    for p in parts[:3]:
        n = ""
        for ch in p:
            if ch.isdigit():
                n += ch
            else:
                break
        nums.append(int(n or 0))
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)  # type: ignore[return-value]


class _FallbackVersion:
    """Lightweight version object for comparison when packaging is unavailable."""

    def __init__(self, t: tuple[int, int, int]):
        self.t = t
        self.major, self.minor, self.micro = t

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, _FallbackVersion):
            return NotImplemented
        return self.t < other.t

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _FallbackVersion):
            return NotImplemented
        return self.t == other.t

    def __repr__(self) -> str:
        return f"_FallbackVersion({self.t})"


def _parse_version(v: str):
    """Parse a version string, using packaging if available, else fallback."""
    try:
        from packaging import version as _pkg_version

        return _pkg_version.parse(v)
    except Exception:
        return _FallbackVersion(_version_tuple_fallback(v))


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
        print(f"::warning title=SDK API::Failed to fetch PyPI metadata: {e}")
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


def _collect_breakages_pairs(objs: Iterable[tuple[object, object]]) -> list:
    """Find breaking changes between pairs of old/new API objects.

    Only reports breakages for public API members.
    """
    import griffe
    from griffe import ExplanationStyle

    breakages = []
    for old, new in objs:
        for br in griffe.find_breaking_changes(old, new):
            obj = getattr(br, "obj", None)
            is_public = getattr(obj, "is_public", True)
            if is_public:
                print(br.explain(style=ExplanationStyle.GITHUB))
                breakages.append(br)
    return breakages


def _extract_exported_names(module) -> set[str]:
    """Extract names exported from a module via __all__.

    Falls back to is_exported attribute or non-underscore names if __all__ is not
    defined.
    """
    names: set[str] = set()

    # Primary: use __all__ if defined
    try:
        all_var = module["__all__"]
    except Exception:
        all_var = None

    if all_var is not None:
        val = getattr(all_var, "value", None)
        elts = getattr(val, "elements", None)
        if elts:
            for el in elts:
                s = getattr(el, "value", None)
                if isinstance(s, str):
                    names.add(s)

    if names:
        return names

    # Fallback: rely on is_exported if available
    for n, m in getattr(module, "members", {}).items():
        if n == "__all__":
            continue
        if getattr(m, "is_exported", False):
            names.add(n)

    if names:
        return names

    # Last resort: non-underscore names
    return {n for n in getattr(module, "members", {}) if not n.startswith("_")}


def _check_version_bump(prev: str, new_version: str, total_breaks: int) -> int:
    """Check if version bump policy is satisfied for breaking changes.

    Policy: Breaking changes require at least a MINOR version bump.

    Returns:
        0 if policy satisfied, 1 if not
    """
    if total_breaks == 0:
        print("No SDK breaking changes detected")
        return 0

    parsed_prev = _parse_version(prev)
    parsed_new = _parse_version(new_version)

    old_major = getattr(parsed_prev, "major", _version_tuple_fallback(prev)[0])
    old_minor = getattr(parsed_prev, "minor", _version_tuple_fallback(prev)[1])
    new_major = getattr(parsed_new, "major", _version_tuple_fallback(new_version)[0])
    new_minor = getattr(parsed_new, "minor", _version_tuple_fallback(new_version)[1])

    # MINOR bump required: same major, higher minor OR higher major
    ok = (new_major > old_major) or (new_major == old_major and new_minor > old_minor)

    if not ok:
        print(
            f"::error title=SDK SemVer::Breaking changes detected ({total_breaks}); "
            f"require at least minor version bump from {old_major}.{old_minor}.x, "
            f"but new is {new_version}"
        )
        return 1

    print(
        f"SDK breaking changes detected ({total_breaks}) and version bump policy "
        f"satisfied ({prev} -> {new_version})"
    )
    return 0


def main() -> int:
    """Main entry point for SDK API breakage detection."""
    ensure_griffe()
    import griffe

    repo_root = os.getcwd()
    current_pyproj = os.path.join(repo_root, PYPROJECT_RELATIVE_PATH)
    new_version = read_version_from_pyproject(current_pyproj)

    include = os.environ.get("SDK_INCLUDE_PATHS", SDK_PACKAGE).split(",")
    include = [p.strip() for p in include if p.strip()]

    prev = get_prev_pypi_version(DISTRIBUTION_NAME, new_version)
    if not prev:
        print(
            f"::warning title=SDK API::No previous {DISTRIBUTION_NAME} release found; "
            "skipping breakage check",
        )
        return 0

    print(f"Comparing {DISTRIBUTION_NAME} {new_version} against {prev}")

    # Load currently checked-out code
    try:
        new_root = griffe.load(
            SDK_PACKAGE, search_paths=[os.path.join(repo_root, "openhands-sdk")]
        )
    except Exception as e:
        print(f"::error title=SDK API::Failed to load current SDK: {e}")
        return 1

    # Load previous from PyPI
    try:
        old_root = griffe.load_pypi(
            package=SDK_PACKAGE,
            distribution=DISTRIBUTION_NAME,
            version_spec=f"=={prev}",
        )
    except Exception as e:
        print(f"::error title=SDK API::Failed to load {prev} from PyPI: {e}")
        return 1

    def resolve(root, dotted: str):
        """Resolve a dotted path to a griffe object."""
        try:
            return root[dotted]
        except Exception:
            pass
        # Try relative to SDK_PACKAGE
        rel = dotted
        if dotted.startswith(SDK_PACKAGE + "."):
            rel = dotted[len(SDK_PACKAGE) + 1 :]
        obj = root
        for part in rel.split("."):
            obj = obj[part]
        return obj

    total_breaks = 0

    # Process top-level exports of openhands.sdk (governed by __all__)
    try:
        old_mod = resolve(old_root, SDK_PACKAGE)
        new_mod = resolve(new_root, SDK_PACKAGE)
        old_exports = _extract_exported_names(old_mod)
        new_exports = _extract_exported_names(new_mod)

        # Check for removed exports
        removed = sorted(old_exports - new_exports)
        for name in removed:
            print(
                f"::error title=SDK API::Removed exported symbol '{name}' from "
                f"{SDK_PACKAGE}.__all__",
            )
            total_breaks += 1

        # Check for signature changes in common exports
        common = sorted(old_exports & new_exports)
        pairs: list[tuple[object, object]] = []
        for name in common:
            try:
                pairs.append((old_mod[name], new_mod[name]))
            except Exception as e:
                print(f"::warning title=SDK API::Unable to resolve symbol {name}: {e}")
        total_breaks += len(_collect_breakages_pairs(pairs))
    except Exception as e:
        print(f"::error title=SDK API::Failed to process top-level exports: {e}")
        return 1

    # Additionally honor include paths that are not the top-level module
    extra_pairs: list[tuple[object, object]] = []
    for path in include:
        if path == SDK_PACKAGE:
            continue
        try:
            old_obj = resolve(old_root, path)
            new_obj = resolve(new_root, path)
            extra_pairs.append((old_obj, new_obj))
        except Exception as e:
            print(f"::warning title=SDK API::Path {path} not found: {e}")

    if extra_pairs:
        total_breaks += len(_collect_breakages_pairs(extra_pairs))

    return _check_version_bump(prev, new_version, total_breaks)


if __name__ == "__main__":
    raise SystemExit(main())
