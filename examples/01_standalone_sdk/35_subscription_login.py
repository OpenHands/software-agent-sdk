"""Example: Using ChatGPT subscription for Codex models.

This example demonstrates how to use your ChatGPT Plus/Pro subscription
to access OpenAI's Codex models without consuming API credits.

The subscription_login() method handles:
- OAuth PKCE authentication flow
- Credential caching (~/.openhands/auth/)
- Automatic token refresh

Supported models:
- gpt-5.2-codex
- gpt-5.2
- gpt-5.1-codex-max
- gpt-5.1-codex-mini

Requirements:
- Active ChatGPT Plus or Pro subscription
- Browser access for initial OAuth login
"""

import os

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


# First time: Opens browser for OAuth login
# Subsequent calls: Reuses cached credentials (auto-refreshes if expired)
llm = LLM.subscription_login(
    vendor="openai",
    model="gpt-5.2-codex",  # or "gpt-5.2", "gpt-5.1-codex-max", "gpt-5.1-codex-mini"
)

# Alternative: Force a fresh login (useful if credentials are stale)
# llm = LLM.subscription_login(vendor="openai", model="gpt-5.2-codex", force_login=True)

# Alternative: Disable auto-opening browser (prints URL to console instead)
# llm = LLM.subscription_login(
#     vendor="openai", model="gpt-5.2-codex", open_browser=False
# )

# Verify subscription mode is active
print(f"Using subscription mode: {llm.is_subscription}")

# Use the LLM with an agent as usual
agent = Agent(
    llm=llm,
    tools=[
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
    ],
)

cwd = os.getcwd()
conversation = Conversation(agent=agent, workspace=cwd)

conversation.send_message("List the files in the current directory.")
conversation.run()
print("Done!")
