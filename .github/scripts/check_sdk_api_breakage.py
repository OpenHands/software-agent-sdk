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


def parse_version(v: str) -> tuple[int, int, int]:
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


def get_prev_pypi_version(pkg: str, current: str | None) -> str | None:
    try:
        with urllib.request.urlopen(
            f"https://pypi.org/pypi/{pkg}/json", timeout=10
        ) as r:
            meta = json.load(r)
    except Exception:
        return None
    releases = list(meta.get("releases", {}).keys())
    if not releases:
        return None
    if current is None:
        # Pick the highest lexicographically by parsed tuple
        releases_sorted = sorted(releases, key=lambda s: parse_version(s), reverse=True)
        return releases_sorted[0]
    cur_t = parse_version(current)
    older = [rv for rv in releases if parse_version(rv) < cur_t]
    if not older:
        return None
    return sorted(older, key=lambda s: parse_version(s), reverse=True)[0]


def ensure_griffe() -> None:
    try:
        import griffe  # noqa: F401
    except Exception:
        sys.stderr.write("griffe not installed; please install griffe[pypi]\n")
        raise


def collect_breakages(objs: Iterable[tuple[object, object]]) -> list:
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
            "skipping breakage check"
        )
        return 0

    # Load currently checked-out code
    new_root = griffe.load(
        sdk_pkg, search_paths=[os.path.join(repo_root, "openhands-sdk")]
    )

    # Load previous from PyPI
    try:
        old_root = griffe.load_pypi("openhands-sdk", version=prev)
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

    pairs = []
    for path in include:
        try:
            old_obj = resolve(old_root, path)
            new_obj = resolve(new_root, path)
            pairs.append((old_obj, new_obj))
        except Exception as e:
            print(f"::warning title=SDK API::Path {path} not found: {e}")
    if not pairs:
        print("::warning title=SDK API::No valid include paths, skipping")
        return 0

    brs = collect_breakages(pairs)
    if not brs:
        print("No SDK breaking changes detected")
        return 0

    # Enforce minor bump policy
    old_major, old_minor, _ = parse_version(prev)
    new_major, new_minor, _ = parse_version(new_version)
    ok = (new_major == old_major) and (new_minor > old_minor)
    if not ok:
        print(
            f"::error title=SDK SemVer::Breaking changes detected; "
            f"require minor version bump from {old_major}.{old_minor}.x, "
            f"but new is {new_version}"
        )
        return 1
    print("SDK breaking changes detected and minor bump policy satisfied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
