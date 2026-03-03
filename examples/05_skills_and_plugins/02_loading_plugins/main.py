"""Example: Loading and Managing Plugins

This example demonstrates plugin loading and management in the SDK:

1. Loading plugins from GitHub via Conversation (PluginSource)
2. Installing plugins to persistent storage (local and GitHub)
3. Listing, loading, and uninstalling plugins

Plugins bundle skills, hooks, and MCP config together.

Supported plugin sources:
- Local path: /path/to/plugin
- GitHub shorthand: github:owner/repo
- Git URL: https://github.com/owner/repo.git
- With ref: branch, tag, or commit SHA
- With repo_path: subdirectory for monorepos

For full documentation, see: https://docs.all-hands.dev/sdk/guides/plugins
"""

import os
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.plugin import (
    PluginFetchError,
    PluginSource,
    install_plugin,
    list_installed_plugins,
    load_installed_plugins,
    uninstall_plugin,
)
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


# Locate example plugin directory
script_dir = Path(__file__).parent
local_plugin_path = script_dir / "example_plugins" / "code-quality"


def demo_conversation_with_github_plugin(llm: LLM) -> None:
    """Demo 1: Load plugin from GitHub via Conversation.

    This demonstrates loading a plugin directly from GitHub using PluginSource.
    The plugin is fetched and loaded lazily when the conversation starts.

    We load the anthropics/skills repository which contains the "document-skills"
    plugin with skills for pptx, xlsx, docx, and pdf document processing.
    """
    print("\n" + "=" * 60)
    print("DEMO 1: Loading plugin from GitHub via Conversation")
    print("=" * 60)

    # Load the anthropics/skills repository which contains the document-skills plugin
    # This plugin bundles multiple document processing skills including pptx
    plugins = [
        PluginSource(
            source="github:anthropics/skills",
            ref="main",
        ),
    ]

    agent = Agent(
        llm=llm,
        tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)],
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            conversation = Conversation(
                agent=agent,
                workspace=tmpdir,
                plugins=plugins,
            )

            # Verify skills were loaded
            skills = (
                conversation.agent.agent_context.skills
                if conversation.agent.agent_context
                else []
            )
            print(f"✓ Loaded {len(skills)} skill(s) from GitHub plugin")
            for skill in skills:
                print(f"  - {skill.name}")

            # Ask a question that uses the pptx skill
            conversation.send_message(
                "What's the best way to create a PowerPoint presentation "
                "programmatically? Check the skill before you answer."
            )

            conversation.run()

        except PluginFetchError as e:
            print(f"⚠ Could not fetch from GitHub: {e}")
            print("  Skipping this demo (network or rate limiting issue)")


def demo_install_local_plugin(installed_dir: Path) -> None:
    """Demo 2: Install a plugin from a local path.

    Useful for development or local-only plugins.
    """
    print("\n" + "=" * 60)
    print("DEMO 2: Installing plugin from local path")
    print("=" * 60)

    info = install_plugin(source=str(local_plugin_path), installed_dir=installed_dir)
    print(f"✓ Installed: {info.name} v{info.version}")
    print(f"  Source: {info.source}")
    print(f"  Path: {info.install_path}")


def demo_install_github_plugin(installed_dir: Path) -> None:
    """Demo 3: Install a plugin from GitHub to persistent storage.

    Demonstrates loading the anthropics/skills repository which contains
    multiple document processing skills (pptx, xlsx, docx, pdf).
    """
    print("\n" + "=" * 60)
    print("DEMO 3: Installing plugin from GitHub")
    print("=" * 60)

    try:
        # Install the anthropics/skills repository (contains document-skills plugin)
        info = install_plugin(
            source="github:anthropics/skills",
            ref="main",
            installed_dir=installed_dir,
        )
        print(f"✓ Installed: {info.name} v{info.version}")
        print(f"  Source: {info.source}")
        print(f"  Resolved ref: {info.resolved_ref}")

        # Show the skills loaded from the plugin
        plugins = load_installed_plugins(installed_dir=installed_dir)
        for plugin in plugins:
            if plugin.name == info.name:
                skills = plugin.get_all_skills()
                print(f"  Skills: {len(skills)}")
                for skill in skills[:5]:  # Show first 5 skills
                    desc = skill.description or "(no description)"
                    print(f"    - {skill.name}: {desc[:50]}...")
                if len(skills) > 5:
                    print(f"    ... and {len(skills) - 5} more skills")

    except PluginFetchError as e:
        print(f"⚠ Could not fetch from GitHub: {e}")
        print("  (Network or rate limiting issue)")


def demo_list_and_load_plugins(installed_dir: Path) -> None:
    """Demo 4: List and load installed plugins."""
    print("\n" + "=" * 60)
    print("DEMO 4: List and load installed plugins")
    print("=" * 60)

    # List installed plugins
    print("Installed plugins:")
    for info in list_installed_plugins(installed_dir=installed_dir):
        print(f"  - {info.name} v{info.version} ({info.source})")

    # Load plugins as Plugin objects
    plugins = load_installed_plugins(installed_dir=installed_dir)
    print(f"\nLoaded {len(plugins)} plugin(s):")
    for plugin in plugins:
        skills = plugin.get_all_skills()
        print(f"  - {plugin.name}: {len(skills)} skill(s)")


def demo_uninstall_plugins(installed_dir: Path) -> None:
    """Demo 5: Uninstall plugins."""
    print("\n" + "=" * 60)
    print("DEMO 5: Uninstalling plugins")
    print("=" * 60)

    for info in list_installed_plugins(installed_dir=installed_dir):
        uninstall_plugin(info.name, installed_dir=installed_dir)
        print(f"✓ Uninstalled: {info.name}")

    remaining = list_installed_plugins(installed_dir=installed_dir)
    print(f"\nRemaining plugins: {len(remaining)}")


# Main execution
if __name__ == "__main__":
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        print("Set LLM_API_KEY to run the full example")
        print("Running install/uninstall demos only...")
        llm = None
    else:
        model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
        llm = LLM(
            usage_id="plugin-demo",
            model=model,
            api_key=SecretStr(api_key),
            base_url=os.getenv("LLM_BASE_URL"),
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        installed_dir = Path(tmpdir) / "installed"
        installed_dir.mkdir()

        # Demo 1: Conversation with GitHub plugin (requires LLM)
        if llm:
            demo_conversation_with_github_plugin(llm)

        # Demo 2-5: Plugin management (no LLM required)
        demo_install_local_plugin(installed_dir)
        demo_install_github_plugin(installed_dir)
        demo_list_and_load_plugins(installed_dir)
        demo_uninstall_plugins(installed_dir)

    print("\n" + "=" * 60)
    print("EXAMPLE COMPLETED SUCCESSFULLY")
    print("=" * 60)

    if llm:
        print(f"EXAMPLE_COST: {llm.metrics.accumulated_cost:.4f}")
    else:
        print("EXAMPLE_COST: 0")
