"""Example: Using Gemini-style file editing tools.

This example demonstrates how to use gemini-style file editing tools
(read_file, write_file, edit, list_directory) instead of the standard
claude-style file_editor tool.

The only difference from the standard setup is replacing:
    Tool(name=FileEditorTool.name)
with:
    *GEMINI_FILE_TOOLS

This is a one-line change that swaps the claude-style file_editor for
gemini-style tools (read_file, write_file, edit, list_directory).
"""

import os

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.gemini import GEMINI_FILE_TOOLS
from openhands.tools.terminal import TerminalTool


# Route logs based on whether we're using a proxy (Vertex route) or direct Gemini
_log_dir = "logs/vertex" if os.getenv("LLM_BASE_URL") else "logs/gemini"
os.makedirs(_log_dir, exist_ok=True)

llm = LLM(
    model=os.getenv("LLM_MODEL", "gemini/gemini-3-pro-preview"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", None),
    log_completions=True,
    log_completions_folder=_log_dir,
)

agent = Agent(
    llm=llm,
    tools=[
        Tool(name=TerminalTool.name),
        *GEMINI_FILE_TOOLS,  # Instead of Tool(name=FileEditorTool.name)
    ],
)

cwd = os.getcwd()
conversation = Conversation(agent=agent, workspace=cwd)

# Ask the agent to create a file, then delete it afterwards
conversation.send_message("Write 3 facts about the current project into FACTS.txt.")
conversation.run()

conversation.send_message("Now delete the FACTS.txt file you just created.")
conversation.run()
print("All done!")
