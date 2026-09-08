"""Reject new dynamic attribute access in SDK source files.

Existing ``getattr``/``setattr`` calls are recorded in a committed baseline so
the hook can be introduced before the cleanup work (tracked in #4903, #4904,
#4905) is finished.  Only calls *not* present in the baseline are reported.

A violation is identified by its file path (relative to the repo root), the
call name, and a hash of the stripped source line.  Keying on the source line
instead of the line number keeps the baseline stable when unrelated edits shift
line numbers, while still flagging genuine edits to an existing call.

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
import sys
from collections import Counter
from pathlib import Path


FORBIDDEN = {"getattr", "setattr"}
BASELINE_FILE = Path(__file__).with_name("forbidden_dynamic_attributes_baseline.json")


def _line_hash(source: str) -> str:
    return hashlib.sha256(source.strip().encode("utf-8")).hexdigest()


def violations(path: Path) -> list[tuple[int, str, str]]:
    """Return ``(lineno, name, line_hash)`` for each forbidden call in *path*."""
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    lines = source.splitlines()
    result: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in FORBIDDEN
        ):
            line_text = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
            result.append((node.lineno, node.func.id, _line_hash(line_text)))
    return result


def _load_baseline() -> Counter[tuple[str, str, str]]:
    if not BASELINE_FILE.exists():
        return Counter()
    data = json.loads(BASELINE_FILE.read_text())
    return Counter((entry["file"], entry["name"], entry["hash"]) for entry in data)


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
    args = parser.parse_args(argv)

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
