#!/usr/bin/env python3
"""
TODO Scanner for OpenHands Automated TODO Management

Scans for configurable TODO comments in Python, TypeScript, Java, and Rust files.
Default identifier: TODO(openhands)
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        # Log to stderr to avoid JSON interference
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger(__name__)


def scan_file_for_todos(
    file_path: Path, todo_identifier: str = "TODO(openhands)"
) -> list[dict]:
    """Scan a single file for configurable TODO comments."""
    # Only scan specific file extensions
    if file_path.suffix.lower() not in {".py", ".ts", ".java", ".rs"}:
        logger.debug(f"Skipping file {file_path} (unsupported extension)")
        return []

    # Skip test files and example files that contain mock TODOs
    file_str = str(file_path)
    if (
        "/test" in file_str
        or "/tests/" in file_str
        or "test_" in file_path.name
        # Skip examples
        or "examples/03_github_workflows/03_todo_management/" in file_str
    ):
        logger.debug(f"Skipping test/example file: {file_path}")
        return []

    logger.debug(f"Scanning file: {file_path}")

    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"Failed to read file {file_path}: {e}")
        return []

    todos = []
    # Escape special regex characters in the identifier
    escaped_identifier = re.escape(todo_identifier)
    todo_pattern = re.compile(rf"{escaped_identifier}(?::\s*(.*))?", re.IGNORECASE)

    for line_num, line in enumerate(lines, 1):
        match = todo_pattern.search(line)
        if match:
            # Extract initial description from the TODO line
            description = match.group(1).strip() if match.group(1) else ""

            # Look ahead for continuation lines that are also comments
            continuation_lines = []
            for next_line_idx in range(line_num, len(lines)):
                next_line = lines[next_line_idx]
                next_stripped = next_line.strip()

                # Check if this line is a comment continuation
                if (
                    next_stripped.startswith("#")
                    and not next_stripped.startswith(f"# {todo_identifier}")
                    # Skip empty comment lines
                    and next_stripped != "#"
                    # Must have content after #
                    and len(next_stripped) > 1
                ):
                    # Extract comment content (remove # and leading whitespace)
                    comment_content = next_stripped[1:].strip()

                    if comment_content:  # Only add non-empty content
                        continuation_lines.append(comment_content)
                elif next_stripped == "#":
                    # Empty comment line - continue looking
                    continue
                else:
                    # Stop at first non-comment line
                    break

            # Combine description with continuation lines
            if continuation_lines:
                if description:
                    full_description = description + " " + " ".join(continuation_lines)
                else:
                    full_description = " ".join(continuation_lines)
            else:
                full_description = description

            todo_item = {
                "file": str(file_path),
                "line": line_num,
                "description": full_description,
            }
            todos.append(todo_item)
            logger.info(f"Found TODO in {file_path}:{line_num}: {full_description}")

    if todos:
        logger.info(f"Found {len(todos)} TODO(s) in {file_path}")
    return todos


def scan_directory(
    directory: Path, todo_identifier: str = "TODO(openhands)"
) -> list[dict]:
    """Recursively scan a directory for configurable TODO comments."""
    logger.info(f"Scanning directory: {directory}")
    all_todos = []

    for root, dirs, files in os.walk(directory):
        # Skip hidden and common ignore directories
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".")
            and d
            not in {
                "__pycache__",
                "node_modules",
                ".venv",
                "venv",
                "build",
                "dist",
            }
        ]

        for file in files:
            file_path = Path(root) / file
            todos = scan_file_for_todos(file_path, todo_identifier)
            all_todos.extend(todos)

    return all_todos


def main():
    """Main function to scan for TODOs and output results."""
    parser = argparse.ArgumentParser(
        description="Scan codebase for configurable TODO comments"
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Directory to scan (default: current directory)",
    )
    parser.add_argument("--output", "-o", help="Output file (default: stdout)")
    parser.add_argument(
        "--identifier",
        "-i",
        default="TODO(openhands)",
        help="TODO identifier to search for (default: TODO(openhands))",
    )

    args = parser.parse_args()

    path = Path(args.directory)
    if not path.exists():
        logger.error(f"Path '{path}' does not exist")
        return 1

    if path.is_file():
        logger.info(f"Starting TODO scan on file: {path}")
        todos = scan_file_for_todos(path, args.identifier)
    else:
        logger.info(f"Starting TODO scan in directory: {path}")
        todos = scan_directory(path, args.identifier)
    logger.info(f"Scan complete. Found {len(todos)} total TODO(s)")
    output = json.dumps(todos, indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Found {len(todos)} TODO(s), written to {args.output}")
    else:
        print(output)

    return 0


if __name__ == "__main__":
    exit(main())
