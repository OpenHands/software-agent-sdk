#!/usr/bin/env python3
"""
Check if all examples in agent-sdk are documented in the docs repository.

This script:
1. Scans the docs repository for references to example files
2. Lists all example Python files in the agent-sdk repository
3. Compares the two sets to find undocumented examples
4. Exits with error code 1 if undocumented examples are found
"""

import os
import re
import sys
from pathlib import Path


def find_documented_examples(docs_path: Path) -> set[str]:
    """
    Find all example file references in the docs repository.

    Searches for patterns like:
    - examples/01_standalone_sdk/02_custom_tools.py
    in MDX files.

    Returns:
        Set of normalized example file paths (relative to agent-sdk root)
    """
    documented_examples: set[str] = set()

    # Pattern to match example file references
    # Matches: examples/[folder1]/[folder2]/[file].py or
    # examples/[folder1]/[file].py
    pattern = r"examples/[\w_]+(?:/[\w_]+)?/[\w_]+\.py"

    for root, _, files in os.walk(docs_path):
        for file in files:
            if file.endswith(".mdx") or file.endswith(".md"):
                file_path = Path(root) / file
                try:
                    content = file_path.read_text(encoding="utf-8")
                    matches = re.findall(pattern, content)
                    for match in matches:
                        # Normalize the path
                        documented_examples.add(match)
                except Exception as e:
                    print(f"Warning: Error reading {file_path}: {e}")
                    continue

    return documented_examples


def find_agent_sdk_examples(agent_sdk_path: Path) -> set[str]:
    """
    Find all example Python files in the agent-sdk repository.

    Excludes examples/03_github_workflows/ since those examples are YAML
    files, not Python files.

    Returns:
        Set of example file paths (relative to agent-sdk root)
    """
    examples: set[str] = set()
    examples_dir = agent_sdk_path / "examples"

    if not examples_dir.exists():
        print(f"Error: Examples directory not found: {examples_dir}")
        sys.exit(1)

    # Find all Python files under examples/
    for root, _, files in os.walk(examples_dir):
        for file in files:
            if file.endswith(".py"):
                file_path = Path(root) / file
                # Get relative path from agent-sdk root
                relative_path = file_path.relative_to(agent_sdk_path)
                relative_path_str = str(relative_path)

                # Skip GitHub workflow examples (those are YAML files, Python
                # files there are just helpers)
                if relative_path_str.startswith("examples/03_github_workflows/"):
                    continue

                # Skip LLM-specific tools examples: these are intentionally not
                # enforced by the docs check. See discussion in PR #1486.
                if relative_path_str.startswith("examples/04_llm_specific_tools/"):
                    continue

                examples.add(relative_path_str)

    return examples


def resolve_paths() -> tuple[Path, Path]:
    """
    Determine agent-sdk root and docs path.

    Priority for docs path:
      1) DOCS_PATH (env override)
      2) $GITHUB_WORKSPACE/docs
      3) agent_sdk_root/'docs'
      4) agent_sdk_root.parent/'docs'

    Returns:
        Tuple of (agent_sdk_root, docs_path)
    """
    # agent-sdk repo root (script is at agent-sdk/.github/scripts/...)
    script_file = Path(__file__).resolve()
    agent_sdk_root = script_file.parent.parent.parent

    candidates: list[Path] = []

    # 1) Explicit env override
    env_override = os.environ.get("DOCS_PATH")
    if env_override:
        candidates.append(Path(env_override).expanduser().resolve())

    # 2) Standard GitHub workspace sibling
    gh_ws = os.environ.get("GITHUB_WORKSPACE")
    if gh_ws:
        candidates.append(Path(gh_ws).resolve() / "docs")

    # 3) Sibling inside the agent-sdk repo root
    candidates.append(agent_sdk_root / "docs")

    # 4) Parent-of-agent-sdk-root layout
    candidates.append(agent_sdk_root.parent / "docs")

    print(f"🔍 Agent SDK root: {agent_sdk_root}")
    print("🔎 Trying docs paths (in order):")
    for p in candidates:
        print(f"   - {p}")

    for p in candidates:
        if p.exists():
            print(f"📁 Using docs path: {p}")
            return agent_sdk_root, p

    # If none exist, fail with a helpful message
    print("❌ Docs path not found in any of the expected locations.")
    print("   Set DOCS_PATH, or checkout the repo to one of the tried paths above.")
    sys.exit(1)


def main() -> None:
    agent_sdk_root, docs_path = resolve_paths()

    print("\n" + "=" * 60)
    print("Checking documented examples...")
    print("=" * 60)

    # Find all examples in agent-sdk
    print("\n📋 Scanning agent-sdk examples...")
    agent_examples = find_agent_sdk_examples(agent_sdk_root)
    print(f"   Found {len(agent_examples)} example file(s)")

    # Find all documented examples in docs
    print("\n📄 Scanning docs repository...")
    documented_examples = find_documented_examples(docs_path)
    print(f"   Found {len(documented_examples)} documented example(s)")

    # Calculate difference
    undocumented = agent_examples - documented_examples

    print("\n" + "=" * 60)
    if undocumented:
        print(f"❌ Found {len(undocumented)} undocumented example(s):")
        print("=" * 60)
        for example in sorted(undocumented):
            print(f"   - {example}")
        print("\n⚠️  Please add documentation for these examples in the docs repo.")
        print("=" * 60)
        print("\n📚 How to Document Examples:")
        print("=" * 60)
        print("1. Clone the docs repository:")
        print("   git clone https://github.com/OpenHands/docs.git")
        print()
        print("2. Create a new .mdx file in sdk/guides/ directory")
        print("   (e.g., sdk/guides/my-feature.mdx)")
        print()
        print("3. Add the example code block with this format:")
        print('   ```python icon="python" expandable examples/path/to/file.py')
        print("   <code will be auto-synced>")
        print("   ```")
        print()
        print("4. See the format documentation at:")
        print(
            "   https://github.com/OpenHands/docs/blob/main/.github/scripts/README.md"
        )
        print()
        print("5. Example documentation files can be found in:")
        print("   https://github.com/OpenHands/docs/tree/main/sdk/guides")
        print()
        print("6. After creating the PR in docs repo, reference it in your")
        print("   agent-sdk PR description.")
        print("=" * 60)
        sys.exit(1)
    else:
        print("✅ All examples are documented!")
        print("=" * 60)
        sys.exit(0)


if __name__ == "__main__":
    main()
