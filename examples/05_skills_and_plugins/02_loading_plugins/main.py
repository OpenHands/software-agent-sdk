"""Example: Loading Plugins via Conversation

Demonstrates the recommended way to load plugins using the `plugins` parameter
on Conversation. Plugins bundle skills, hooks, and MCP config together.

For full documentation, see: https://docs.all-hands.dev/sdk/guides/plugins
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


# Locate example plugin directory
script_dir = Path(__file__).parent
plugin_path = script_dir / "example_plugins" / "code-quality"

# Define plugins to load
# Supported sources: local path, "github:owner/repo", or git URL
# Optional: ref (branch/tag/commit), repo_path (for monorepos)
plugins = [
    PluginSource(source=str(plugin_path)),
    # PluginSource(source="github:org/security-plugin", ref="v2.0.0"),
    # PluginSource(source="github:org/monorepo", repo_path="plugins/logging"),
]

# Check for API key
api_key = os.getenv("LLM_API_KEY")
if not api_key:
    print("Set LLM_API_KEY to run this example")
    print("EXAMPLE_COST: 0")
    sys.exit(0)

# Configure LLM and Agent
model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
llm = LLM(
    usage_id="plugin-demo",
    model=model,
    api_key=SecretStr(api_key),
    base_url=os.getenv("LLM_BASE_URL"),
)
agent = Agent(
    llm=llm, tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)]
)

# Create conversation with plugins - skills, MCP config, and hooks are merged
# Note: Plugins are loaded lazily on first send_message() or run() call
with tempfile.TemporaryDirectory() as tmpdir:
    conversation = Conversation(
        agent=agent,
        workspace=tmpdir,
        plugins=plugins,
    )

    # Test: The "lint" keyword triggers the python-linting skill
    # This first send_message() call triggers lazy plugin loading
    conversation.send_message("How do I lint Python code? Brief answer please.")

    # Verify skills were loaded from the plugin (after lazy loading)
    skills = (
        conversation.agent.agent_context.skills
        if conversation.agent.agent_context
        else []
    )
    print(f"Loaded {len(skills)} skill(s) from plugins")

    conversation.run()

    print(f"EXAMPLE_COST: {llm.metrics.accumulated_cost:.4f}")
