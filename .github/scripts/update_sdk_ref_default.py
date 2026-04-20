#!/usr/bin/env python3
"""Update the sdk_ref default value in run-eval.yml.

This script updates the default SDK reference version in the run-eval workflow
to match a new release version.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_EVAL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "run-eval.yml"

# Pattern to match the sdk_ref default line
# Matches: "default: vX.Y.Z" with optional prerelease suffix like -rc1, -beta.1
SDK_REF_PATTERN = re.compile(
    r"^(\s*default:\s*v)[\d]+\.[\d]+\.[\d]+(-[a-zA-Z0-9.]+)?(\s*)$"
)


def update_sdk_ref_default(new_version: str, dry_run: bool = False) -> bool:
    """Update the sdk_ref default in run-eval.yml.

    Args:
        new_version: The new version (without 'v' prefix, e.g., "1.12.0")
        dry_run: If True, print what would change without modifying the file

    Returns:
        True if successful, False otherwise
    """
    if not RUN_EVAL_WORKFLOW.exists():
        print(f"❌ File not found: {RUN_EVAL_WORKFLOW}", file=sys.stderr)
        return False

    content = RUN_EVAL_WORKFLOW.read_text()
    lines = content.splitlines(keepends=True)

    # Find the sdk_ref input section and its default line
    in_sdk_ref_section = False
    updated = False
    old_version = None

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Track when we enter the sdk_ref input section
        if stripped == "sdk_ref:":
            in_sdk_ref_section = True
            continue

        # Track when we exit the sdk_ref section (another input starts)
        if (
            in_sdk_ref_section
            and stripped.endswith(":")
            and not stripped.startswith("default")
        ):
            in_sdk_ref_section = False

        # Update the default line within the sdk_ref section
        if in_sdk_ref_section:
            match = SDK_REF_PATTERN.match(line)
            if match:
                old_version = line.strip().replace("default: ", "")
                new_line = f"{match.group(1)}{new_version}{match.group(3) or ''}"
                if not line.endswith("\n") and lines[i].endswith("\n"):
                    new_line += "\n"
                elif line.endswith("\n"):
                    new_line += "\n"
                lines[i] = new_line
                updated = True
                break

    if not updated:
        print("❌ Could not find sdk_ref default line to update", file=sys.stderr)
        return False

    if dry_run:
        print(f"Would update sdk_ref default: {old_version} → v{new_version}")
        return True

    # Write the updated content
    RUN_EVAL_WORKFLOW.write_text("".join(lines))
    print(f"✅ Updated sdk_ref default: {old_version} → v{new_version}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update the sdk_ref default value in run-eval.yml"
    )
    parser.add_argument(
        "version",
        help="New version (without 'v' prefix, e.g., '1.12.0')",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without modifying the file",
    )
    args = parser.parse_args()

    # Validate version format
    version_pattern = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$")
    if not version_pattern.match(args.version):
        print(
            f"❌ Invalid version format: {args.version}. "
            "Expected: X.Y.Z or X.Y.Z-suffix",
            file=sys.stderr,
        )
        return 1

    success = update_sdk_ref_default(args.version, dry_run=args.dry_run)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
