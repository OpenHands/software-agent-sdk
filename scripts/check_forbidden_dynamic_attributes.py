"""Reject new dynamic attribute access in SDK source files.

Existing ``getattr``/``setattr`` calls are recorded in a committed baseline so
the hook can be introduced before the cleanup work (tracked in #4903, #4904,
#4905) is finished.  Only calls *not* present in the baseline are reported.

A violation is identified by its file path (relative to the repo root), the
call name, and a hash of the stripped source line.  Keying on the source line
instead of the line number keeps the baseline stable when unrelated edits shift
line numbers, while still flagging genuine edits to an existing call.

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


def _load_baseline() -> set[tuple[str, str, str]]:
    if not BASELINE_FILE.exists():
        return set()
    data = json.loads(BASELINE_FILE.read_text())
    return {(entry["file"], entry["name"], entry["hash"]) for entry in data}


def _write_baseline(entries: list[tuple[str, str, str]]) -> None:
    payload = [
        {"file": file, "name": name, "hash": digest}
        for file, name, digest in sorted(entries)
    ]
    BASELINE_FILE.write_text(json.dumps(payload, indent=4) + "\n")


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

    current = _current_entries(args.paths)

    if args.update_baseline:
        _write_baseline([(file, name, digest) for file, name, digest, _ in current])
        print(f"Baseline updated: {len(current)} existing violations recorded.")
        return 0

    baseline = _load_baseline()
    current_keys = {(file, name, digest) for file, name, digest, _ in current}
    passed_files = set(args.paths)
    new_violations = [
        (file, line, name)
        for file, name, digest, line in current
        if (file, name, digest) not in baseline
    ]
    # Only flag staleness for files actually being checked, so subset runs
    # (e.g. pre-commit on changed files) don't report unrelated drift.
    stale = {entry for entry in baseline - current_keys if entry[0] in passed_files}

    for file, line, name in sorted(new_violations):
        print(f"{file}:{line}: forbidden dynamic attribute call: {name}")
    if stale:
        count = len(stale)
        unit = "entry is" if count == 1 else "entries are"
        print(f"note: {count} baseline {unit} stale; run --update-baseline to refresh.")
    if new_violations:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
