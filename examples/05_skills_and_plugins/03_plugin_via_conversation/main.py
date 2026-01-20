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
example_plugins_dir = script_dir.parent / "02_loading_plugins" / "example_plugins"
plugin_path = example_plugins_dir / "code-quality"

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
    sys.exit(0)

# Configure LLM and Agent
model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
llm = LLM(usage_id="plugin-demo", model=model, api_key=SecretStr(api_key))
agent = Agent(
    llm=llm, tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)]
)

# Create conversation with plugins - skills, MCP config, and hooks are merged
with tempfile.TemporaryDirectory() as tmpdir:
    conversation = Conversation(
        agent=agent,
        workspace=tmpdir,
        plugins=plugins,
    )

    # Verify skills were loaded from the plugin
    skills = (
        conversation.agent.agent_context.skills
        if conversation.agent.agent_context
        else []
    )
    print(f"Loaded {len(skills)} skill(s) from plugins")

    # Test: The "lint" keyword triggers the python-linting skill
    conversation.send_message("How do I lint Python code? Brief answer please.")
    conversation.run()

    print(f"Cost: ${llm.metrics.accumulated_cost:.4f}")
