"""Example: Loading and Managing Plugins

This example demonstrates plugin loading and lifecycle management in the SDK:

1. Loading a plugin from GitHub via Conversation (PluginSource)
2. Installing plugins to persistent storage (local and GitHub)
3. Listing tracked plugins and loading only the enabled ones
4. Inspecting the `.installed.json` metadata file and `enabled` flag
5. Disabling and re-enabling a plugin without reinstalling it
6. Uninstalling plugins from persistent storage

Plugins bundle skills, hooks, and MCP config together.

Supported plugin sources:
- Local path: /path/to/plugin
- GitHub shorthand: github:owner/repo
- Git URL: https://github.com/owner/repo.git
- With ref: branch, tag, or commit SHA
- With repo_path: subdirectory for monorepos

For full documentation, see: https://docs.all-hands.dev/sdk/guides/plugins
"""

import json
import os
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.plugin import (
    PluginFetchError,
    PluginSource,
    disable_plugin,
    enable_plugin,
    install_plugin,
    list_installed_plugins,
    load_installed_plugins,
    uninstall_plugin,
)
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


script_dir = Path(__file__).parent
local_plugin_path = script_dir / "example_plugins" / "code-quality"


def print_state(label: str, installed_dir: Path) -> None:
    """Print tracked, loaded, and persisted plugin state."""
    print(f"\n{label}")
    print("-" * len(label))

    installed = list_installed_plugins(installed_dir=installed_dir)
    print("Tracked plugins:")
    for info in installed:
        print(f"  - {info.name} (enabled={info.enabled}, source={info.source})")

    loaded = load_installed_plugins(installed_dir=installed_dir)
    print(f"Loaded plugins: {[plugin.name for plugin in loaded]}")

    metadata = json.loads((installed_dir / ".installed.json").read_text())
    print("Metadata file:")
    print(json.dumps(metadata, indent=2))


def demo_conversation_with_github_plugin(llm: LLM) -> None:
    """Demo 1: Load plugin from GitHub via Conversation."""
    print("\n" + "=" * 60)
    print("DEMO 1: Loading plugin from GitHub via Conversation")
    print("=" * 60)

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

            conversation.send_message(
                "What's the best way to create a PowerPoint presentation "
                "programmatically? Check the skill before you answer."
            )

            skills = (
                conversation.agent.agent_context.skills
                if conversation.agent.agent_context
                else []
            )
            print(f"✓ Loaded {len(skills)} skill(s) from GitHub plugin")
            for skill in skills[:5]:
                print(f"  - {skill.name}")
            if len(skills) > 5:
                print(f"  ... and {len(skills) - 5} more skills")

            if conversation.resolved_plugins:
                print("Resolved plugin refs:")
                for resolved in conversation.resolved_plugins:
                    print(f"  - {resolved.source} @ {resolved.resolved_ref}")

            conversation.run()

        except PluginFetchError as e:
            print(f"⚠ Could not fetch from GitHub: {e}")
            print("  Skipping this demo (network or rate limiting issue)")


def demo_install_local_plugin(installed_dir: Path) -> str:
    """Demo 2: Install a plugin from a local path."""
    print("\n" + "=" * 60)
    print("DEMO 2: Installing plugin from local path")
    print("=" * 60)

    info = install_plugin(source=str(local_plugin_path), installed_dir=installed_dir)
    print(f"✓ Installed: {info.name} v{info.version}")
    print(f"  Source: {info.source}")
    print(f"  Path: {info.install_path}")
    return info.name


def demo_install_github_plugin(installed_dir: Path) -> None:
    """Demo 3: Install a plugin from GitHub to persistent storage."""
    print("\n" + "=" * 60)
    print("DEMO 3: Installing plugin from GitHub")
    print("=" * 60)

    try:
        info = install_plugin(
            source="github:anthropics/skills",
            ref="main",
            installed_dir=installed_dir,
        )
        print(f"✓ Installed: {info.name} v{info.version}")
        print(f"  Source: {info.source}")
        print(f"  Resolved ref: {info.resolved_ref}")

        plugins = load_installed_plugins(installed_dir=installed_dir)
        for plugin in plugins:
            if plugin.name != info.name:
                continue

            skills = plugin.get_all_skills()
            print(f"  Skills: {len(skills)}")
            for skill in skills[:5]:
                desc = skill.description or "(no description)"
                print(f"    - {skill.name}: {desc[:50]}...")
            if len(skills) > 5:
                print(f"    ... and {len(skills) - 5} more skills")

    except PluginFetchError as e:
        print(f"⚠ Could not fetch from GitHub: {e}")
        print("  (Network or rate limiting issue)")


def demo_list_and_load_plugins(installed_dir: Path) -> None:
    """Demo 4: List tracked plugins and load the enabled ones."""
    print("\n" + "=" * 60)
    print("DEMO 4: Listing and loading installed plugins")
    print("=" * 60)

    print("Tracked plugins:")
    for info in list_installed_plugins(installed_dir=installed_dir):
        print(f"  - {info.name} v{info.version} (enabled={info.enabled})")

    plugins = load_installed_plugins(installed_dir=installed_dir)
    print(f"\nLoaded {len(plugins)} plugin(s):")
    for plugin in plugins:
        skills = plugin.get_all_skills()
        print(f"  - {plugin.name}: {len(skills)} skill(s)")


def demo_enable_disable_plugin(installed_dir: Path, plugin_name: str) -> None:
    """Demo 5: Disable then re-enable a plugin without reinstalling it."""
    print("\n" + "=" * 60)
    print("DEMO 5: Disabling and re-enabling a plugin")
    print("=" * 60)

    print_state("Before disable", installed_dir)

    assert disable_plugin(plugin_name, installed_dir=installed_dir) is True
    print_state("After disable", installed_dir)
    assert plugin_name not in [
        plugin.name for plugin in load_installed_plugins(installed_dir=installed_dir)
    ]

    metadata = json.loads((installed_dir / ".installed.json").read_text())
    assert metadata["extensions"][plugin_name]["enabled"] is False

    assert enable_plugin(plugin_name, installed_dir=installed_dir) is True
    print_state("After re-enable", installed_dir)

    metadata = json.loads((installed_dir / ".installed.json").read_text())
    assert metadata["extensions"][plugin_name]["enabled"] is True
    assert plugin_name in [
        plugin.name for plugin in load_installed_plugins(installed_dir=installed_dir)
    ]


def demo_uninstall_plugins(installed_dir: Path) -> None:
    """Demo 6: Uninstall all tracked plugins."""
    print("\n" + "=" * 60)
    print("DEMO 6: Uninstalling plugins")
    print("=" * 60)

    for info in list_installed_plugins(installed_dir=installed_dir):
        uninstall_plugin(info.name, installed_dir=installed_dir)
        print(f"✓ Uninstalled: {info.name}")

    remaining = list_installed_plugins(installed_dir=installed_dir)
    print(f"\nRemaining plugins: {len(remaining)}")


if __name__ == "__main__":
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        print("Set LLM_API_KEY to run the full example")
        print("Running install and lifecycle demos only...")
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
        installed_dir = Path(tmpdir) / "installed-plugins"
        installed_dir.mkdir()

        if llm:
            demo_conversation_with_github_plugin(llm)

        local_plugin_name = demo_install_local_plugin(installed_dir)
        demo_install_github_plugin(installed_dir)
        demo_list_and_load_plugins(installed_dir)
        demo_enable_disable_plugin(installed_dir, local_plugin_name)
        demo_uninstall_plugins(installed_dir)

    print("\n" + "=" * 60)
    print("EXAMPLE COMPLETED SUCCESSFULLY")
    print("=" * 60)

    if llm:
        print(f"EXAMPLE_COST: {llm.metrics.accumulated_cost:.4f}")
    else:
        print("EXAMPLE_COST: 0")
