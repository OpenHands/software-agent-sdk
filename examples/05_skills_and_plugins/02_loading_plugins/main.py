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
import sys
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext, Conversation
from openhands.sdk.plugin import Plugin
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


# Get the directory containing this script
script_dir = Path(__file__).parent
example_plugins_dir = script_dir / "example_plugins"

# =============================================================================
# Part 1: Loading a Single Plugin
# =============================================================================
print("=" * 80)
print("Part 1: Loading a Single Plugin")
print("=" * 80)

plugin_path = example_plugins_dir / "code-quality"
print(f"Loading plugin from: {plugin_path}")

plugin = Plugin.load(plugin_path)

print("\nPlugin loaded successfully!")
print(f"  Name: {plugin.name}")
print(f"  Version: {plugin.version}")
print(f"  Description: {plugin.description}")

# Show manifest details (extra fields are accessible via model_extra)
print("\nManifest details:")
print(f"  Author: {plugin.manifest.author}")
extra = plugin.manifest.model_extra or {}
print(f"  License: {extra.get('license', 'N/A')}")
print(f"  Repository: {extra.get('repository', 'N/A')}")

# =============================================================================
# Part 2: Exploring Plugin Components
# =============================================================================
print("\n" + "=" * 80)
print("Part 2: Exploring Plugin Components")
print("=" * 80)

# Skills
print(f"\nSkills ({len(plugin.skills)}):")
for skill in plugin.skills:
    desc = skill.description or ""
    print(f"  - {skill.name}: {desc[:60]}...")
    if skill.trigger:
        print(f"    Triggers: {skill.trigger}")

# Hooks
print(f"\nHooks: {'Configured' if plugin.hooks else 'None'}")
if plugin.hooks:
    for event_type, matchers in plugin.hooks.hooks.items():
        print(f"  - {event_type}: {len(matchers)} matcher(s)")

# MCP Config
print(f"\nMCP Config: {'Configured' if plugin.mcp_config else 'None'}")
if plugin.mcp_config is not None:
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

# =============================================================================
# Part 3: Loading All Plugins from a Directory
# =============================================================================
print("\n" + "=" * 80)
print("Part 3: Loading All Plugins from a Directory")
print("=" * 80)

plugins = Plugin.load_all(example_plugins_dir)
print(f"\nLoaded {len(plugins)} plugin(s) from {example_plugins_dir}")
for p in plugins:
    print(f"  - {p.name} v{p.version}")

# =============================================================================
# Part 4: Using Plugin Components with an Agent
# =============================================================================
print("\n" + "=" * 80)
print("Part 4: Using Plugin Components with an Agent")
print("=" * 80)

# Check for API key
api_key = os.getenv("LLM_API_KEY")
if not api_key:
    print("Skipping agent demo (LLM_API_KEY not set)")
    print("\nTo run the full demo, set the LLM_API_KEY environment variable:")
    print("  export LLM_API_KEY=your-api-key")
    sys.exit(0)

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
    mcp_config=plugin.mcp_config or {},  # Use MCP servers from plugin
)

# Create a temporary directory for the demo
with tempfile.TemporaryDirectory() as tmpdir:
    # Create conversation with plugin hooks
    conversation = Conversation(
        agent=agent,
        workspace=tmpdir,
        hook_config=plugin.hooks,  # Use hooks from plugin
    )

    # Demo 1: Test the skill (triggered by "lint" keyword)
    print("\n--- Demo 1: Skill Triggering ---")
    print("Sending message with 'lint' keyword to trigger skill...")
    conversation.send_message(
        "How do I lint Python code? Just give a brief explanation."
    )
    conversation.run()

    # Demo 2: Test hooks by using file_editor (triggers PostToolUse hook)
    print("\n--- Demo 2: Hook Execution ---")
    print("Creating a file to trigger PostToolUse hook on file_editor...")
    conversation.send_message(
        "Create a file called hello.py with a simple print statement."
    )
    conversation.run()

    # Demo 3: Test MCP by using fetch tool
    print("\n--- Demo 3: MCP Tool Usage ---")
    print("Using fetch MCP tool to retrieve a URL...")
    conversation.send_message(
        "Use the fetch tool to get the content from https://httpbin.org/get "
        "and tell me what the 'origin' field contains."
    )
    conversation.run()

    # Verify hooks executed by checking the hook log file
    print("\n--- Verifying Hook Execution ---")
    hook_log_path = os.path.join(tmpdir, ".hook_log")
    if os.path.exists(hook_log_path):
        print("Hook log file found! Contents:")
        with open(hook_log_path) as f:
            for line in f:
                print(f"  {line.strip()}")
    else:
        print("No hook log file found (hooks may not have executed)")

    print(f"\nTotal cost: ${llm.metrics.accumulated_cost:.4f}")
