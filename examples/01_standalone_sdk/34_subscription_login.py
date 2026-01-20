"""Example: Using ChatGPT subscription for Codex models.

This example demonstrates how to use your ChatGPT Plus/Pro subscription
to access OpenAI's Codex models without consuming API credits.

The subscription_login() method handles:
- OAuth PKCE authentication flow
- Credential caching (~/.local/share/openhands/auth/)
- Automatic token refresh

Supported models:
- gpt-5.2-codex (default)
- gpt-5.2
- gpt-5.1-codex-max
- gpt-5.1-codex-mini

Requirements:
- Active ChatGPT Plus or Pro subscription
- Browser access for initial OAuth login
"""

import os
import sys

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.llm.streaming import ModelResponseStream
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


def on_token(chunk: ModelResponseStream) -> None:
    for choice in chunk.choices:
        delta = getattr(choice, "delta", None)
        if delta is None:
            continue
        content = getattr(delta, "content", None)
        if isinstance(content, str) and content:
            sys.stdout.write(content)
            sys.stdout.flush()


# First time: Opens browser for OAuth login
# Subsequent calls: Reuses cached credentials (auto-refreshes if expired)
llm = LLM.subscription_login(
    model="gpt-5.2-codex",  # or "gpt-5.2", "gpt-5.1-codex-max", "gpt-5.1-codex-mini"
    stream=True,
)

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
conversation = Conversation(agent=agent, workspace=cwd, token_callbacks=[on_token])

conversation.send_message("List the files in the current directory.")
conversation.run()
print("Done!")


# Alternative: Force a fresh login (useful if credentials are stale)
# llm = LLM.subscription_login(model="gpt-5.2-codex", force_login=True)

# Alternative: Disable auto-opening browser (prints URL to console instead)
# llm = LLM.subscription_login(model="gpt-5.2-codex", open_browser=False)
