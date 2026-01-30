#!/usr/bin/env python3
"""
Check if all LLM exception subclasses in the SDK are documented in the docs repository.

This script:
1. Scans the SDK's llm/exceptions/types.py for all exception classes
2. Scans the docs repository for references to these exception classes
3. Compares the two sets to find undocumented exceptions
4. Exits with error code 1 if undocumented exceptions are found
"""

import ast
import os
import re
import sys
from pathlib import Path


def find_sdk_exceptions(sdk_path: Path) -> dict[str, str]:
    """
    Find all exception classes defined in the SDK's llm/exceptions/types.py.

    Returns:
        Dict mapping exception class name to its base class name
    """
    exceptions_file = (
        sdk_path
        / "openhands-sdk"
        / "openhands"
        / "sdk"
        / "llm"
        / "exceptions"
        / "types.py"
    )

    if not exceptions_file.exists():
        print(f"Error: Exceptions file not found: {exceptions_file}")
        sys.exit(1)

    content = exceptions_file.read_text(encoding="utf-8")
    tree = ast.parse(content)

    exceptions: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            # Get the base class name(s)
            bases = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    bases.append(base.id)
                elif isinstance(base, ast.Attribute):
                    bases.append(base.attr)

            # Check if this is an exception class
            # (ends with Error or Exception, or inherits from one)
            is_exception = (
                node.name.endswith("Error")
                or node.name.endswith("Exception")
                or node.name.endswith("Cancelled")
                or any(
                    b.endswith("Error") or b.endswith("Exception") or b == "Exception"
                    for b in bases
                )
            )

            if is_exception:
                base_class = bases[0] if bases else "object"
                exceptions[node.name] = base_class

    return exceptions


def find_documented_exceptions(docs_path: Path) -> set[str]:
    """
    Find all exception class references in the docs repository.

    Searches for exception class names in MDX/MD files.

    Returns:
        Set of exception class names found in documentation
    """
    documented_exceptions: set[str] = set()

    # Pattern to match exception class names
    # (PascalCase ending with Error, Exception, or Cancelled)
    pattern = r"\b([A-Z][a-zA-Z]*(?:Error|Exception|Cancelled))\b"

    for root, _, files in os.walk(docs_path):
        for file in files:
            if file.endswith(".mdx") or file.endswith(".md"):
                file_path = Path(root) / file
                try:
                    content = file_path.read_text(encoding="utf-8")
                    matches = re.findall(pattern, content)
                    for match in matches:
                        documented_exceptions.add(match)
                except Exception as e:
                    print(f"Warning: Error reading {file_path}: {e}")
                    continue

    return documented_exceptions


def resolve_paths() -> tuple[Path, Path]:
    """
    Determine SDK root and docs path.

    Priority for docs path:
      1) DOCS_PATH (env override)
      2) $GITHUB_WORKSPACE/docs
      3) sdk_root/'docs'
      4) sdk_root.parent/'docs'

    Returns:
        Tuple of (sdk_root, docs_path)
    """
    # SDK repo root (script is at sdk/.github/scripts/...)
    script_file = Path(__file__).resolve()
    sdk_root = script_file.parent.parent.parent

    candidates: list[Path] = []

    # 1) Explicit env override
    env_override = os.environ.get("DOCS_PATH")
    if env_override:
        candidates.append(Path(env_override).expanduser().resolve())

    # 2) Standard GitHub workspace sibling
    gh_ws = os.environ.get("GITHUB_WORKSPACE")
    if gh_ws:
        candidates.append(Path(gh_ws).resolve() / "docs")

    # 3) Sibling inside the SDK repo root
    candidates.append(sdk_root / "docs")

    # 4) Parent-of-SDK-root layout
    candidates.append(sdk_root.parent / "docs")

    print(f"🔍 SDK root: {sdk_root}")
    print("🔎 Trying docs paths (in order):")
    for p in candidates:
        print(f"   - {p}")

    for p in candidates:
        if p.exists():
            print(f"📁 Using docs path: {p}")
            return sdk_root, p

    # If none exist, fail with a helpful message
    print("❌ Docs path not found in any of the expected locations.")
    print("   Set DOCS_PATH, or checkout the repo to one of the tried paths above.")
    sys.exit(1)


def main() -> None:
    sdk_root, docs_path = resolve_paths()

    print("\n" + "=" * 60)
    print("Checking documented LLM exceptions...")
    print("=" * 60)

    # Find all exceptions in SDK
    print("\n📋 Scanning SDK exceptions...")
    sdk_exceptions = find_sdk_exceptions(sdk_root)
    print(f"   Found {len(sdk_exceptions)} exception class(es):")
    for name, base in sorted(sdk_exceptions.items()):
        print(f"      - {name} (inherits from {base})")

    # Find all documented exceptions in docs
    print("\n📄 Scanning docs repository...")
    documented_exceptions = find_documented_exceptions(docs_path)
    print(f"   Found {len(documented_exceptions)} documented exception reference(s)")

    # Calculate difference - only check SDK exceptions
    sdk_exception_names = set(sdk_exceptions.keys())
    undocumented = sdk_exception_names - documented_exceptions

    print("\n" + "=" * 60)
    if undocumented:
        print(f"❌ Found {len(undocumented)} undocumented exception(s):")
        print("=" * 60)
        for exception in sorted(undocumented):
            base = sdk_exceptions[exception]
            print(f"   - {exception} (inherits from {base})")
        print("\n⚠️  Please add documentation for these exceptions in the docs repo.")
        print("=" * 60)
        print("\n📚 How to Document Exceptions:")
        print("=" * 60)
        print("1. Clone the docs repository:")
        print("   git clone https://github.com/OpenHands/docs.git")
        print()
        print("2. Edit the exception handling guide:")
        print("   sdk/guides/llm-error-handling.mdx")
        print()
        print("3. Add the exception to the 'Exception reference' section")
        print("   with a description of when it's raised.")
        print()
        print("4. See existing documentation at:")
        print(
            "   https://github.com/OpenHands/docs/blob/main/sdk/guides/llm-error-handling.mdx"
        )
        print("=" * 60)
        sys.exit(1)
    else:
        print("✅ All SDK exceptions are documented!")
        print("=" * 60)
        sys.exit(0)


if __name__ == "__main__":
    main()
