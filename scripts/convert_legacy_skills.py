#!/usr/bin/env python3
"""Convert legacy OpenHands skills to AgentSkills standard format.

This script converts single .md skill files to the AgentSkills directory format:
- Creates skill-name/ directory with SKILL.md
- Converts mcp_tools frontmatter to .mcp.json files
- Preserves OpenHands-specific fields (triggers, inputs) for compatibility

Usage:
    # Convert a single skill file
    python convert_legacy_skills.py skill.md --output-dir ./converted/

    # Convert all skills in a directory
    python convert_legacy_skills.py ./skills/ --output-dir ./converted/

    # Dry run (show what would be converted)
    python convert_legacy_skills.py ./skills/ --output-dir ./converted/ --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from openhands.sdk.context.skills.conversion import (
    convert_legacy_skill,
    convert_skills_directory,
)


def main():
    parser = argparse.ArgumentParser(
        description="Convert legacy OpenHands skills to AgentSkills standard format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Source skill file (.md) or directory containing skill files",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        required=True,
        help="Output directory for converted skills",
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Show what would be converted without writing files",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove output directory before converting",
    )

    args = parser.parse_args()

    # Clean output directory if requested
    if args.clean and args.output_dir.exists() and not args.dry_run:
        print(f"Cleaning output directory: {args.output_dir}")
        shutil.rmtree(args.output_dir)

    # Create output directory
    if not args.dry_run:
        args.output_dir.mkdir(parents=True, exist_ok=True)

    # Convert based on source type
    if args.source.is_file():
        result = convert_legacy_skill(
            args.source, args.output_dir, dry_run=args.dry_run
        )
        if result:
            print(f"\nSuccess: Created {result}")
        else:
            sys.exit(1)
    elif args.source.is_dir():
        results = convert_skills_directory(
            args.source, args.output_dir, dry_run=args.dry_run
        )
        if not results:
            print("No skills were converted", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Error: Source not found: {args.source}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
