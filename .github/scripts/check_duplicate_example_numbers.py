#!/usr/bin/env python3
"""
Check for duplicate example numbers in the examples directory.

This script ensures that within each examples subdirectory, no two files or
folders share the same numeric prefix (e.g., two files both starting with "04_").

Exit codes:
    0 - No duplicates found
    1 - Duplicates found
"""

import re
import sys
from collections import defaultdict
from pathlib import Path


def find_duplicate_numbers(examples_dir: Path) -> dict[str, list[str]]:
    """
    Find duplicate example numbers within each subdirectory.

    Returns:
        Dictionary mapping subdirectory paths to lists of duplicate entries.
        Only includes subdirectories that have duplicates.
    """
    duplicates: dict[str, list[str]] = {}

    # Pattern to extract leading number from filename/dirname
    # e.g., "04" from "04_foo.py"
    number_pattern = re.compile(r"^(\d+)_")

    for subdir in sorted(examples_dir.iterdir()):
        if not subdir.is_dir():
            continue

        # Skip hidden directories
        if subdir.name.startswith("."):
            continue

        # Group entries by their numeric prefix
        number_to_entries: dict[str, list[str]] = defaultdict(list)

        for entry in subdir.iterdir():
            # Skip hidden files/directories
            if entry.name.startswith("."):
                continue

            match = number_pattern.match(entry.name)
            if match:
                number = match.group(1)
                number_to_entries[number].append(entry.name)

        # Find numbers with multiple entries
        subdir_duplicates = []
        for number, entries in sorted(number_to_entries.items()):
            if len(entries) > 1:
                subdir_duplicates.extend(sorted(entries))

        if subdir_duplicates:
            relative_subdir = str(subdir.relative_to(examples_dir.parent))
            duplicates[relative_subdir] = subdir_duplicates

    return duplicates


def main() -> None:
    # Find the examples directory relative to this script
    script_file = Path(__file__).resolve()
    repo_root = script_file.parent.parent.parent
    examples_dir = repo_root / "examples"

    if not examples_dir.exists():
        print(f"Error: Examples directory not found: {examples_dir}")
        sys.exit(1)

    print("=" * 60)
    print("Checking for duplicate example numbers...")
    print("=" * 60)
    print(f"\n📁 Scanning: {examples_dir}\n")

    duplicates = find_duplicate_numbers(examples_dir)

    if duplicates:
        print("❌ Found duplicate example numbers:\n")
        for subdir, entries in sorted(duplicates.items()):
            print(f"  {subdir}/")
            for entry in entries:
                print(f"    - {entry}")
            print()

        print("=" * 60)
        print("⚠️  Please renumber the examples to remove duplicates.")
        print("   Each example should have a unique number within its folder.")
        print("=" * 60)
        sys.exit(1)
    else:
        print("✅ No duplicate example numbers found!")
        print("=" * 60)
        sys.exit(0)


if __name__ == "__main__":
    main()
