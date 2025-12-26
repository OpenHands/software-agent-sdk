#!/usr/bin/env python3
import json
import os
import sys
import tomllib
import urllib.request
from collections.abc import Iterable


def read_version_from_pyproject(path: str) -> str:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    proj = data.get("project", {})
    v = proj.get("version")
    if not v:
        raise SystemExit("Could not read version from pyproject")
    return str(v)


def _version_tuple_fallback(v: str) -> tuple[int, int, int]:
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


def _parse_version(v: str):
    try:
        from packaging import version as _pkg_version

        return _pkg_version.parse(v)
    except Exception:
        # Fallback: return a lightweight object with comparable tuple behavior
        class _V:
            def __init__(self, t: tuple[int, int, int]):
                self.t = t
                self.major, self.minor, self.micro = t

            def __lt__(self, other: object) -> bool:
                if not isinstance(other, _V):
                    return NotImplemented
                return self.t < other.t

            def __eq__(self, other: object) -> bool:
                if not isinstance(other, _V):
                    return NotImplemented
                return self.t == other.t

        return _V(_version_tuple_fallback(v))


def get_prev_pypi_version(pkg: str, current: str | None) -> str | None:
    req = urllib.request.Request(
        url=f"https://pypi.org/pypi/{pkg}/json",
        headers={"User-Agent": "openhands-sdk-api-check/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            meta = json.load(r)
    except Exception:
        return None

    releases = list(meta.get("releases", {}).keys())
    if not releases:
        return None

    def _sort_key(s: str):
        v = _parse_version(s)
        # packaging.Version supports comparison; fallback also supports it
        return v

    if current is None:
        releases_sorted = sorted(releases, key=_sort_key, reverse=True)
        return releases_sorted[0]

    cur_parsed = _parse_version(current)
    older = [rv for rv in releases if _parse_version(rv) < cur_parsed]
    if not older:
        return None
    return sorted(older, key=_sort_key, reverse=True)[0]


def ensure_griffe() -> None:
    try:
        import griffe  # noqa: F401
    except Exception:
        sys.stderr.write("griffe not installed; please install griffe[pypi]\n")
        raise


def _collect_breakages_pairs(objs: Iterable[tuple[object, object]]) -> list:
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
    names: set[str] = set()
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
    # Fallback: rely on is_exported if available, else non-underscore names
    for n, m in getattr(module, "members", {}).items():
        if n == "__all__":
            continue
        if getattr(m, "is_exported", False):
            names.add(n)
    if names:
        return names
    return {n for n in getattr(module, "members", {}) if not n.startswith("_")}


def main() -> int:
    ensure_griffe()
    import griffe

    repo_root = os.getcwd()
    sdk_pkg = "openhands.sdk"
    current_pyproj = os.path.join(repo_root, "openhands-sdk", "pyproject.toml")
    new_version = read_version_from_pyproject(current_pyproj)

    include = os.environ.get("SDK_INCLUDE_PATHS", sdk_pkg).split(",")
    include = [p.strip() for p in include if p.strip()]

    prev = get_prev_pypi_version("openhands-sdk", new_version)
    if not prev:
        print(
            "::warning title=SDK API::No previous openhands-sdk release found; "
            "skipping breakage check",
        )
        return 0

    # Load currently checked-out code
    new_root = griffe.load(
        sdk_pkg, search_paths=[os.path.join(repo_root, "openhands-sdk")]
    )

    # Load previous from PyPI
    try:
        old_root = griffe.load_pypi(
            package="openhands.sdk",
            distribution="openhands-sdk",
            version_spec=f"=={prev}",
        )
    except Exception as e:
        print(f"::warning title=SDK API::Failed to load previous from PyPI: {e}")
        return 0

    def resolve(root, dotted: str):
        # Try absolute path first
        try:
            return root[dotted]
        except Exception:
            pass
        # Try relative to sdk_pkg
        rel = dotted
        if dotted.startswith(sdk_pkg + "."):
            rel = dotted[len(sdk_pkg) + 1 :]
        obj = root
        for part in rel.split("."):
            obj = obj[part]
        return obj

    total_breaks = 0

    # Always process top-level exports of openhands.sdk
    try:
        old_mod = resolve(old_root, sdk_pkg)
        new_mod = resolve(new_root, sdk_pkg)
        old_exports = _extract_exported_names(old_mod)
        new_exports = _extract_exported_names(new_mod)

        removed = sorted(old_exports - new_exports)
        for name in removed:
            print(
                f"::error title=SDK API::Removed exported symbol '{name}' from "
                + f"{sdk_pkg}.__all__",
            )
            total_breaks += 1

        common = sorted(old_exports & new_exports)
        pairs: list[tuple[object, object]] = []
        for name in common:
            try:
                pairs.append((old_mod[name], new_mod[name]))
            except Exception as e:  # pragma: no cover - unexpected griffe model state
                print(f"::warning title=SDK API::Unable to resolve symbol {name}: {e}")
        total_breaks += len(_collect_breakages_pairs(pairs))
    except Exception as e:
        print(f"::warning title=SDK API::Failed to process top-level exports: {e}")

    # Additionally honor include paths that are not the top-level module
    extra_pairs: list[tuple[object, object]] = []
    for path in include:
        if path == sdk_pkg:
            continue
        try:
            old_obj = resolve(old_root, path)
            new_obj = resolve(new_root, path)
            extra_pairs.append((old_obj, new_obj))
        except Exception as e:
            print(f"::warning title=SDK API::Path {path} not found: {e}")

    if extra_pairs:
        total_breaks += len(_collect_breakages_pairs(extra_pairs))

    if total_breaks == 0:
        print("No SDK breaking changes detected")
        return 0

    # Enforce MINOR bump for breaking changes (policy agreed)
    parsed_prev = _parse_version(prev)
    parsed_new = _parse_version(new_version)
    # Both packaging.Version and fallback expose major/minor
    old_major = getattr(parsed_prev, "major", _version_tuple_fallback(prev)[0])
    old_minor = getattr(parsed_prev, "minor", _version_tuple_fallback(prev)[1])
    new_major = getattr(parsed_new, "major", _version_tuple_fallback(new_version)[0])
    new_minor = getattr(parsed_new, "minor", _version_tuple_fallback(new_version)[1])

    ok = (new_major == old_major) and (new_minor > old_minor)
    if not ok:
        print(
            "::error title=SDK SemVer::Breaking changes detected; require minor "
            f"version bump from {old_major}.{old_minor}.x, but new is {new_version}"
        )
        return 1

    print("SDK breaking changes detected and minor bump policy satisfied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
