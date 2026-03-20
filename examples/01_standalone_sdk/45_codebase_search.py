"""Codebase search using Morph's WarpGrep.

Natural-language code search backed by an LLM sub-agent that uses ripgrep,
file reads, and directory listing under the hood.  Two tools are provided:

  - ``codebase_search``          — search a local repository
  - ``github_codebase_search``   — search a public GitHub repository

Requirements:
  - MORPH_API_KEY  : Get from https://morphllm.com/dashboard/api-keys
  - LLM_API_KEY    : Your LLM provider API key
  - Node.js 18+    : Required by the MCP server (``npx``)
"""

import os

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.codebase_search import register_codebase_search_tools
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool

# 1. Register the Morph search tools (explicit, not automatic)
register_codebase_search_tools()

# 2. Configure the LLM
llm = LLM(
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", None),
)

# 3. Build the agent with search + editing tools
agent = Agent(
    llm=llm,
    tools=[
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
        # Morph search tools — pass api_key here or set MORPH_API_KEY env var
        Tool(name="codebase_search"),
        Tool(name="github_codebase_search"),
    ],
)

# 4. Run a conversation
cwd = os.getcwd()
conversation = Conversation(agent=agent, workspace=cwd)
conversation.send_message(
    "Use codebase_search to find how errors are handled in this project, "
    "then summarize what you found."
)
conversation.run()

print(f"EXAMPLE_COST: {llm.metrics.accumulated_cost}")
