"""Example: Loading Plugins

This example demonstrates how to load plugins that bundle multiple components:
- Skills (specialized knowledge and workflows)
- Hooks (event handlers for tool lifecycle)
- MCP configuration (external tool servers)
- Agents (specialized agent definitions)
- Commands (slash commands)

Plugins follow the Claude Code plugin structure for compatibility.
See the example_plugins/ directory for a complete plugin structure.
"""

import os
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext, Conversation
from openhands.sdk.plugin import Plugin
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


def main():
    # Get the directory containing this script
    script_dir = Path(__file__).parent
    example_plugins_dir = script_dir / "example_plugins"

    # =========================================================================
    # Part 1: Loading a Single Plugin
    # =========================================================================
    print("=" * 80)
    print("Part 1: Loading a Single Plugin")
    print("=" * 80)

    plugin_path = example_plugins_dir / "code-quality"
    print(f"Loading plugin from: {plugin_path}")

    plugin = Plugin.load(plugin_path)

    print(f"\nPlugin loaded successfully!")
    print(f"  Name: {plugin.name}")
    print(f"  Version: {plugin.version}")
    print(f"  Description: {plugin.description}")

    # Show manifest details
    print("\nManifest details:")
    print(f"  Author: {plugin.manifest.author}")
    print(f"  License: {plugin.manifest.license}")
    print(f"  Repository: {plugin.manifest.repository}")

    # =========================================================================
    # Part 2: Exploring Plugin Components
    # =========================================================================
    print("\n" + "=" * 80)
    print("Part 2: Exploring Plugin Components")
    print("=" * 80)

    # Skills
    print(f"\nSkills ({len(plugin.skills)}):")
    for skill in plugin.skills:
        print(f"  - {skill.name}: {skill.description[:60]}...")
        if skill.trigger:
            print(f"    Triggers: {skill.trigger}")

    # Hooks
    print(f"\nHooks: {'Configured' if plugin.hooks else 'None'}")
    if plugin.hooks:
        for event_type, matchers in plugin.hooks.hooks.items():
            print(f"  - {event_type}: {len(matchers)} matcher(s)")

    # MCP Config
    print(f"\nMCP Config: {'Configured' if plugin.mcp_config else 'None'}")
    if plugin.mcp_config:
        servers = plugin.mcp_config.get("mcpServers", {})
        for server_name in servers:
            print(f"  - {server_name}")

    # Agents
    print(f"\nAgents ({len(plugin.agents)}):")
    for agent_def in plugin.agents:
        print(f"  - {agent_def.name}: {agent_def.description[:60]}...")

    # Commands
    print(f"\nCommands ({len(plugin.commands)}):")
    for cmd in plugin.commands:
        print(f"  - /{cmd.name}: {cmd.description[:60]}...")

    # =========================================================================
    # Part 3: Loading All Plugins from a Directory
    # =========================================================================
    print("\n" + "=" * 80)
    print("Part 3: Loading All Plugins from a Directory")
    print("=" * 80)

    plugins = Plugin.load_all(example_plugins_dir)
    print(f"\nLoaded {len(plugins)} plugin(s) from {example_plugins_dir}")
    for p in plugins:
        print(f"  - {p.name} v{p.version}")

    # =========================================================================
    # Part 4: Using Plugin Components with an Agent
    # =========================================================================
    print("\n" + "=" * 80)
    print("Part 4: Using Plugin Components with an Agent")
    print("=" * 80)

    # Check for API key
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        print("Skipping agent demo (LLM_API_KEY not set)")
        print("\nTo run the full demo, set the LLM_API_KEY environment variable:")
        print("  export LLM_API_KEY=your-api-key")
        return

    # Configure LLM
    model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
    llm = LLM(
        usage_id="plugin-demo",
        model=model,
        api_key=SecretStr(api_key),
    )

    # Create agent context with plugin skills
    agent_context = AgentContext(
        skills=plugin.skills,
        load_public_skills=False,  # Only use plugin skills for this demo
    )

    # Create agent with tools and plugin MCP config
    tools = [
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
    ]
    agent = Agent(
        llm=llm,
        tools=tools,
        agent_context=agent_context,
        mcp_config=plugin.mcp_config,  # Use MCP servers from plugin
    )

    # Create conversation with plugin hooks
    conversation = Conversation(
        agent=agent,
        workspace=os.getcwd(),
        hook_config=plugin.hooks,  # Use hooks from plugin
    )

    # Test the skill (triggered by "lint" keyword)
    print("\nSending message with 'lint' keyword to trigger skill...")
    conversation.send_message(
        "How do I lint Python code? Just explain, don't run any commands."
    )
    conversation.run()

    print(f"\nTotal cost: ${llm.metrics.accumulated_cost:.4f}")


if __name__ == "__main__":
    main()
