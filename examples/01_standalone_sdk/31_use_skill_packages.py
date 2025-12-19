"""Example: Using Skill Packages with OpenHands SDK

This example demonstrates how to load and use skills from installed
OpenHands skill packages. Skill packages provide a way to distribute
and reuse collections of skills across projects.

Prerequisites:
    1. Install a skill package:
       pip install openhands-simple-code-review

    2. Configure packages to load:
       Create .openhands/packages.yaml with:
         packages:
           - simple-code-review

    3. Or use the package loading functions directly in code.

For more information on creating skill packages, see:
    https://github.com/OpenHands/package-poc
"""

import asyncio
from pathlib import Path

from openhands.sdk.context.skills import (
    get_skill_package,
    list_skill_packages,
    load_configured_package_skills,
    load_skills_from_package,
)


async def list_available_packages():
    """List all installed skill packages."""
    print("=" * 60)
    print("Installed Skill Packages")
    print("=" * 60)

    packages = list_skill_packages()

    if not packages:
        print("No skill packages installed.")
        print("\nTo install a skill package:")
        print("  pip install openhands-simple-code-review")
        return

    for pkg in packages:
        metadata = pkg["descriptor"]["metadata"]
        skills = pkg["descriptor"]["spec"]["skills"]

        print(f"\n📦 {metadata['displayName']} (v{metadata['version']})")
        print(f"   Name: {pkg['name']}")
        print(f"   Description: {metadata.get('description', 'N/A')}")
        print(f"   Skills: {len(skills)}")

        for skill in skills:
            triggers = skill.get("triggers", [])
            trigger_str = ", ".join(triggers) if triggers else "always active"
            print(f"     • {skill['name']} (triggers: {trigger_str})")


async def show_package_details(package_name: str):
    """Show detailed information about a specific package."""
    print("\n" + "=" * 60)
    print(f"Package Details: {package_name}")
    print("=" * 60)

    pkg = get_skill_package(package_name)

    if pkg is None:
        print(f"Package '{package_name}' not found.")
        return

    metadata = pkg["descriptor"]["metadata"]
    spec = pkg["descriptor"]["spec"]

    print(f"\nDisplay Name: {metadata['displayName']}")
    print(f"Version: {metadata['version']}")
    print(f"Author: {metadata.get('author', 'N/A')}")
    print(f"License: {metadata.get('license', 'N/A')}")
    print(f"Repository: {metadata.get('repository', 'N/A')}")

    print(f"\nTags: {', '.join(metadata.get('tags', []))}")

    print("\nSkills:")
    for skill in spec["skills"]:
        print(f"  • {skill['name']}")
        print(f"    Path: {skill['path']}")
        print(f"    Type: {skill.get('type', 'unknown')}")
        if "triggers" in skill:
            print(f"    Triggers: {', '.join(skill['triggers'])}")


async def load_and_inspect_skills(package_name: str):
    """Load skills from a package and inspect them."""
    print("\n" + "=" * 60)
    print(f"Loading Skills from: {package_name}")
    print("=" * 60)

    try:
        repo_skills, knowledge_skills = load_skills_from_package(package_name)

        print(
            f"\nLoaded {len(repo_skills)} repo skills and "
            f"{len(knowledge_skills)} knowledge skills"
        )

        if repo_skills:
            print("\nRepo Skills (always active):")
            for name, skill in repo_skills.items():
                print(f"  • {name}")
                print(f"    Source: {skill.source}")
                print(f"    Content length: {len(skill.content)} chars")

        if knowledge_skills:
            print("\nKnowledge Skills (trigger-activated):")
            for name, skill in knowledge_skills.items():
                print(f"  • {name}")
                print(f"    Source: {skill.source}")
                print(f"    Trigger: {skill.trigger}")
                print(f"    Content length: {len(skill.content)} chars")

    except ValueError as e:
        print(f"Error: {e}")


async def use_packages_with_agent():
    """Create an agent with skills loaded from packages."""
    print("\n" + "=" * 60)
    print("Using Skill Packages with an Agent")
    print("=" * 60)

    # Load skills from configured packages
    repo_skills, knowledge_skills = load_configured_package_skills()

    print(f"\nLoaded {len(knowledge_skills)} knowledge skills from configured packages")

    if not knowledge_skills:
        print("\nNo skills loaded. To configure packages:")
        print("  1. Create .openhands/packages.yaml")
        print("  2. Add package names under 'packages:' key")
        print("  3. Example:")
        print("       packages:")
        print("         - simple-code-review")
        return

    print("\nAvailable skills:")
    for name, skill in knowledge_skills.items():
        print(f"  • {name} (source: {skill.source})")

    # You can now use these skills with an agent
    # For example:
    # agent = Agent(model="gpt-4")
    # agent.add_skills(list(knowledge_skills.values()))


async def create_packages_config():
    """Create a sample packages.yaml configuration file."""
    config_path = Path(".openhands/packages.yaml")

    if config_path.exists():
        print(f"\nConfiguration already exists at: {config_path}")
        with open(config_path) as f:
            print(f.read())
        return

    config_content = """# OpenHands Skill Packages Configuration
#
# List skill packages to load. Package names should match the entry point
# names defined in the packages' pyproject.toml files.

packages:
  # Add your packages here, e.g.:
  # - simple-code-review
  # - awesome-skills
"""

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_content)

    print(f"\nCreated sample configuration at: {config_path}")
    print("\nEdit this file to add packages you want to load.")


async def main():
    """Run the skill packages example."""
    print("\n🎯 OpenHands Skill Packages Example")
    print("=" * 60)

    # 1. List all available packages
    await list_available_packages()

    # 2. Check if we have any packages installed
    packages = list_skill_packages()

    if packages:
        # Show details for the first package
        first_package = packages[0]["name"]
        await show_package_details(first_package)

        # Load and inspect skills from the first package
        await load_and_inspect_skills(first_package)

    # 3. Try to use configured packages
    await use_packages_with_agent()

    # 4. Show how to create configuration
    print("\n" + "=" * 60)
    print("Configuration Setup")
    print("=" * 60)
    await create_packages_config()

    print("\n" + "=" * 60)
    print("Next Steps")
    print("=" * 60)
    print("""
1. Install skill packages:
   pip install openhands-simple-code-review

2. Configure which packages to load:
   Edit .openhands/packages.yaml and add package names

3. Run this example again to see loaded skills

4. Create your own skill packages:
   See: https://github.com/OpenHands/package-poc

For more information on skill packages:
   https://github.com/OpenHands/package-poc/blob/main/doc/tutorial-flow.md
""")


if __name__ == "__main__":
    asyncio.run(main())
