"""Reject new dynamic attribute access in SDK source files.

Existing ``getattr``/``setattr`` and ``obj.__dict__.get`` calls are recorded
in a committed baseline so the hook can be introduced before the cleanup work
(tracked in #4903, #4904, #4905) is finished. Only calls *not* present in the
baseline are reported.

A violation is identified by its file path (relative to the repo root), the
call name, and a hash of the full call source segment (not just the first
physical line).  Keying on the complete expression keeps the baseline stable
when unrelated edits shift line numbers, while still flagging genuine edits
to an existing call — including argument changes on subsequent lines.

The baseline is a *multiset* (occurrence counts are preserved), so adding
another copy of an already-baselined call is still flagged.

Regenerate the baseline after removing existing calls with::

    uv run python scripts/check_forbidden_dynamic_attributes.py \\
        --update-baseline $(find openhands-sdk -name '*.py')
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path


FORBIDDEN = {"getattr", "setattr"}
DICT_GET = "__dict__.get"
BASELINE_FILE = Path(__file__).with_name("forbidden_dynamic_attributes_baseline.json")


def _forbidden_call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN:
        return node.func.id
    if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "__dict__"
    ):
        return DICT_GET
    return None


def _segment_hash(source: str, node: ast.Call) -> str:
    """Hash the full source segment of *node*, not just its first line."""
    segment = ast.get_source_segment(source, node)
    if segment is not None:
        text = segment
    else:
        # Fallback: join all physical lines the node spans.
        lines = source.splitlines()
        end = getattr(node, "end_lineno", node.lineno) or node.lineno
        text = "\n".join(lines[node.lineno - 1 : end])
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def violations(path: Path) -> list[tuple[int, str, str]]:
    """Return ``(lineno, name, segment_hash)`` for each forbidden call in *path*."""
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    result: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _forbidden_call_name(node)
        if name is not None:
            result.append((node.lineno, name, _segment_hash(source, node)))
    return result


Baseline = Counter[tuple[str, str, str]]


def _parse_baseline(content: str) -> Baseline:
    data = json.loads(content)
    return Counter((entry["file"], entry["name"], entry["hash"]) for entry in data)


def _load_baseline() -> Baseline:
    if not BASELINE_FILE.exists():
        return Counter()
    return _parse_baseline(BASELINE_FILE.read_text())


def _load_baseline_from_git(ref: str) -> Baseline:
    root = Path(__file__).resolve().parent.parent
    relative_path = BASELINE_FILE.resolve().relative_to(root)
    result = subprocess.run(
        ["git", "show", f"{ref}:{relative_path.as_posix()}"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return _parse_baseline(result.stdout)


def _baseline_additions(reference: Baseline, current: Baseline) -> Baseline:
    return current - reference


def _write_baseline(entries: list[tuple[str, str, str]]) -> None:
    payload = [
        {"file": file, "name": name, "hash": digest}
        for file, name, digest in sorted(entries)
    ]
    BASELINE_FILE.write_text(json.dumps(payload, indent=4) + "\n")


SDK_ROOT = "openhands-sdk"


def _discover_sdk_files() -> list[str]:
    """Return all Python files under the SDK root, relative to the repo root."""
    root = Path(__file__).resolve().parent.parent
    return [str(p.relative_to(root)) for p in sorted(root.glob(f"{SDK_ROOT}/**/*.py"))]


def _current_entries(
    paths: list[str],
) -> list[tuple[str, str, str, int]]:
    """Return all current violations across *paths* as ``(file, name, hash, line)``."""
    entries: list[tuple[str, str, str, int]] = []
    for raw_path in paths:
        for line, name, digest in violations(Path(raw_path)):
            entries.append((raw_path, name, digest, line))
    return entries


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Python files to check")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline from the current violations and exit 0.",
    )
    parser.add_argument(
        "--baseline-ref",
        help="Reject baseline entries not present at this Git reference.",
    )
    args = parser.parse_args(argv)

    if args.update_baseline and args.baseline_ref:
        parser.error("--update-baseline cannot be combined with --baseline-ref")

    if args.baseline_ref:
        baseline = _load_baseline()
        additions = _baseline_additions(
            _load_baseline_from_git(args.baseline_ref), baseline
        )
        if additions:
            for (file, name, _digest), count in sorted(additions.items()):
                print(f"{file}: baseline adds {count} forbidden {name} allowance(s)")
            print("error: the forbidden dynamic attributes baseline may only shrink")
            return 1

    # When no paths are given (e.g. via pre-commit with pass_filenames: false),
    # auto-discover all SDK Python files so deletions are caught.
    paths = args.paths if args.paths else _discover_sdk_files()

    current = _current_entries(paths)

    if args.update_baseline:
        _write_baseline([(file, name, digest) for file, name, digest, _ in current])
        print(f"Baseline updated: {len(current)} existing violations recorded.")
        return 0

    baseline = _load_baseline()
    current_counter = Counter((file, name, digest) for file, name, digest, _ in current)
    checked_files = set(paths)

    # New violations: occurrences that exceed the baseline count for each key.
    new_violations: list[tuple[str, int, str]] = []
    for file, name, digest, line in current:
        key = (file, name, digest)
        if current_counter[key] > baseline.get(key, 0):
            new_violations.append((file, line, name))
            current_counter[key] -= 1  # count only the excess as new

    # Stale: baseline entries no longer present in the current code.  When the
    # checker runs on all SDK files (auto-discovery), entries for deleted files
    # are also flagged; subset runs only report drift for files they checked.
    stale = {
        key
        for key, count in (baseline - current_counter).items()
        if key[0] in checked_files or not Path(key[0]).exists()
    }

    for file, line, name in sorted(new_violations):
        print(f"{file}:{line}: forbidden dynamic attribute call: {name}")
    if stale:
        count = len(stale)
        unit = "entry is" if count == 1 else "entries are"
        print(f"error: {count} baseline {unit} stale; run --update-baseline to refresh")
    if new_violations or stale:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
