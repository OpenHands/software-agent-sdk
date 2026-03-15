"""Example: Mixed Marketplace with Local and Remote Skills

This example demonstrates how to create a marketplace that includes both:
1. Local skills hosted in your project directory
2. Remote skills from GitHub (OpenHands/extensions repository)

The marketplace.json schema supports source paths in these formats:
- Local paths: ./path, ../path, /absolute/path, ~/path, file:///path
- GitHub URLs: https://github.com/{owner}/{repo}/blob/{branch}/{path}

This pattern is useful for teams that want to:
- Maintain their own custom skills locally
- Reference specific skills from remote repositories
- Create a curated skill set for their specific workflows

Directory Structure:
    43_mixed_marketplace_skills/
    ├── .plugin/
    │   └── marketplace.json     # Marketplace with local and remote skills
    ├── skills/
    │   └── greeting-helper/
    │       └── SKILL.md         # Local skill content
    ├── main.py                  # This file
    └── README.md                # Documentation

Usage:
    # Install all skills from marketplace to ~/.openhands/skills/installed/
    python main.py --install

    # Force reinstall (overwrite existing)
    python main.py --install --force

    # Show installed skills
    python main.py --list
"""

import sys
from pathlib import Path

from openhands.sdk.plugin import Marketplace
from openhands.sdk.skills import (
    install_skills_from_marketplace,
    list_installed_skills,
)


def main():
    script_dir = Path(__file__).parent

    if "--list" in sys.argv:
        # List installed skills
        print("=" * 80)
        print("Installed Skills")
        print("=" * 80)
        installed = list_installed_skills()
        if not installed:
            print("\nNo skills installed.")
            print("Run with --install to install skills from the marketplace.")
        else:
            for info in installed:
                desc = (info.description or "No description")[:60]
                print(f"\n  {info.name}")
                print(f"    Description: {desc}...")
                print(f"    Source: {info.source}")
        return

    if "--install" in sys.argv:
        # Install skills from marketplace
        print("=" * 80)
        print("Installing Skills from Marketplace")
        print("=" * 80)
        print(f"\nMarketplace directory: {script_dir}")

        force = "--force" in sys.argv
        installed = install_skills_from_marketplace(script_dir, force=force)

        print(f"\n\nInstalled {len(installed)} skills:")
        for info in installed:
            print(f"  - {info.name}")

        # Show all installed skills
        print("\n" + "=" * 80)
        print("All Installed Skills")
        print("=" * 80)
        all_installed = list_installed_skills()
        for info in all_installed:
            desc = (info.description or "No description")[:50]
            print(f"  - {info.name}: {desc}...")
        return

    # Default: show marketplace info
    print("=" * 80)
    print("Marketplace Information")
    print("=" * 80)
    print(f"\nMarketplace directory: {script_dir}")

    marketplace = Marketplace.load(script_dir)
    print(f"Name: {marketplace.name}")
    print(f"Description: {marketplace.description}")
    print(f"Skills defined: {len(marketplace.skills)}")

    print("\nSkills:")
    for entry in marketplace.skills:
        source_type = "remote" if entry.source.startswith("http") else "local"
        print(f"  - {entry.name} ({source_type})")
        print(f"    Source: {entry.source}")
        if entry.description:
            print(f"    Description: {entry.description}")

    print("\n" + "-" * 80)
    print("Usage:")
    print("  python main.py --install        # Install all skills")
    print("  python main.py --install --force # Force reinstall")
    print("  python main.py --list           # List installed skills")


if __name__ == "__main__":
    main()
    print("EXAMPLE_COST: 0")
