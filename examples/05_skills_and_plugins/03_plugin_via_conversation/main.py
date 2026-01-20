"""Example: Loading Plugins via Conversation

This example demonstrates the recommended way to load plugins using the
`plugins` parameter on the Conversation class. This approach:

1. Automatically loads and merges multiple plugins
2. Handles skills, MCP config, and hooks automatically
3. Works with both LocalConversation and RemoteConversation
4. Supports GitHub repositories, git URLs, and local paths

This is the preferred approach over manually calling Plugin.load() and
merging components by hand.
"""

import os
import sys
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.plugin import PluginSource
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


# Get the directory containing this script
script_dir = Path(__file__).parent
example_plugins_dir = script_dir.parent / "02_loading_plugins" / "example_plugins"

# =============================================================================
# Part 1: Creating a Conversation with Plugins (Local Path)
# =============================================================================
print("=" * 80)
print("Part 1: Loading Plugin via Conversation (Local Path)")
print("=" * 80)

# The plugin source can be:
# - Local path: "/path/to/plugin" or "./relative/path"
# - GitHub shorthand: "github:owner/repo"
# - Git URL: "https://github.com/org/repo.git"
# - With ref: PluginSource(source="github:org/repo", ref="v1.0.0")
# - From monorepo: PluginSource(source="github:org/repo", repo_path="plugins/my-plugin")

plugin_path = example_plugins_dir / "code-quality"
print(f"Plugin source: {plugin_path}")

# Create plugin source specification
plugin_spec = PluginSource(source=str(plugin_path))
print(f"Created PluginSource: {plugin_spec.model_dump()}")

# =============================================================================
# Part 2: Multiple Plugins Example
# =============================================================================
print("\n" + "=" * 80)
print("Part 2: Loading Multiple Plugins")
print("=" * 80)

# You can specify multiple plugins - they are loaded in order
# Skills and MCP configs: last plugin wins (override by name/key)
# Hooks: all hooks concatenate (all run)
plugins = [
    PluginSource(source=str(plugin_path)),
    # Add more plugins as needed:
    # PluginSource(source="github:org/security-plugin", ref="v2.0.0"),
    # PluginSource(source="github:org/monorepo", repo_path="plugins/logging"),
]

print(f"Configured {len(plugins)} plugin(s):")
for p in plugins:
    print(f"  - {p.source}")
    if p.ref:
        print(f"    ref: {p.ref}")
    if p.repo_path:
        print(f"    repo_path: {p.repo_path}")

# =============================================================================
# Part 3: Using Plugins with an Agent
# =============================================================================
print("\n" + "=" * 80)
print("Part 3: Using Plugins with an Agent")
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

# Create agent with tools (no need to manually configure MCP or skills)
tools = [
    Tool(name=TerminalTool.name),
    Tool(name=FileEditorTool.name),
]
agent = Agent(
    llm=llm,
    tools=tools,
    # Note: No agent_context.skills or mcp_config needed here!
    # The plugins parameter on Conversation handles this automatically.
)

# Create a temporary directory for the demo
with tempfile.TemporaryDirectory() as tmpdir:
    # Create conversation WITH plugins parameter
    # This is the key difference from the manual approach!
    conversation = Conversation(
        agent=agent,
        workspace=tmpdir,
        plugins=plugins,  # <-- Plugins are loaded and merged automatically
    )

    print("\nConversation created with plugins loaded!")
    agent_context = conversation.agent.agent_context
    skills = agent_context.skills if agent_context else []
    print(f"Agent skills: {len(skills)}")

    # Show loaded skills
    print("\nLoaded skills from plugins:")
    for skill in skills:
        print(f"  - {skill.name}")

    # Demo: Test the skill (triggered by "lint" keyword)
    print("\n--- Demo: Skill Triggering ---")
    print("Sending message with 'lint' keyword to trigger skill...")
    conversation.send_message(
        "How do I lint Python code? Just give a brief explanation."
    )
    conversation.run()

    # Demo: Test hooks by using file_editor
    print("\n--- Demo: Hook Execution ---")
    print("Creating a file to trigger PostToolUse hook on file_editor...")
    conversation.send_message(
        "Create a file called hello.py with a simple print statement."
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

# =============================================================================
# Summary
# =============================================================================
print("\n" + "=" * 80)
print("Summary: Plugin Loading via Conversation")
print("=" * 80)
print("""
The `plugins` parameter on Conversation provides:

1. Automatic loading: Plugins are fetched and loaded automatically
2. Automatic merging: Skills, MCP configs, and hooks are merged
3. Multi-plugin support: Load multiple plugins, last one wins for conflicts
4. Git support: Use GitHub shorthand, git URLs, or local paths
5. Version pinning: Use `ref` to pin to a specific branch/tag/commit
6. Monorepo support: Use `repo_path` for plugins in subdirectories

Example:
    conversation = Conversation(
        agent=agent,
        workspace="./workspace",
        plugins=[
            PluginSource(source="github:org/security-plugin", ref="v2.0.0"),
            PluginSource(source="github:org/monorepo", repo_path="plugins/audit"),
            PluginSource(source="/local/custom-plugin"),
        ],
    )
""")
