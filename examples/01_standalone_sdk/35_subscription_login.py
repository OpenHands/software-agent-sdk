"""Example: Using ChatGPT subscription for Codex models.

This example demonstrates how to use your ChatGPT Plus/Pro subscription
to access OpenAI's Codex models without consuming API credits.

The subscription_login() method handles:
- OAuth PKCE authentication flow
- Device-code authentication for remote/headless environments
- Credential caching (~/.openhands/auth/)
- Automatic token refresh

Supported models:
- gpt-5.2-codex
- gpt-5.2
- gpt-5.1-codex-max
- gpt-5.1-codex-mini

Requirements:
- Active ChatGPT Plus or Pro subscription
- Browser access for initial OAuth login, or another browser/device for
  device-code login

Environment variables:
- OPENHANDS_SUBSCRIPTION_MODEL: Model to use (default: gpt-5.2-codex)
- OPENHANDS_SUBSCRIPTION_AUTH_METHOD: "browser" or "device_code"
  (default: browser)
- OPENHANDS_SUBSCRIPTION_FORCE_LOGIN: Set to "1" to force fresh login
- SUBSCRIPTION_LOGIN_ONLY: Set to "1" to verify login without running an agent
"""

import os
from typing import Literal

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


AuthMethod = Literal["browser", "device_code"]


# First time: Opens browser for OAuth login
# Subsequent calls: Reuses cached credentials (auto-refreshes if expired)
model = os.getenv("OPENHANDS_SUBSCRIPTION_MODEL", "gpt-5.2-codex")
auth_method_env = os.getenv("OPENHANDS_SUBSCRIPTION_AUTH_METHOD", "browser")
if auth_method_env not in ("browser", "device_code"):
    raise ValueError(
        "OPENHANDS_SUBSCRIPTION_AUTH_METHOD must be 'browser' or 'device_code'"
    )
auth_method: AuthMethod = auth_method_env
force_login = os.getenv("OPENHANDS_SUBSCRIPTION_FORCE_LOGIN") == "1"

llm = LLM.subscription_login(
    vendor="openai",
    model=model,  # or "gpt-5.2", "gpt-5.1-codex-max", "gpt-5.1-codex-mini"
    auth_method=auth_method,
    force_login=force_login,
)

# Alternative: Force a fresh login (useful if credentials are stale)
# llm = LLM.subscription_login(vendor="openai", model="gpt-5.2-codex", force_login=True)

# Alternative: Disable auto-opening browser (prints URL to console instead)
# llm = LLM.subscription_login(
#     vendor="openai", model="gpt-5.2-codex", open_browser=False
# )
#
# Alternative: Use device-code login for remote/headless environments
# llm = LLM.subscription_login(
#     vendor="openai",
#     model="gpt-5.2-codex",
#     auth_method="device_code",
#     force_login=True,
# )

# Verify subscription mode is active
print(f"Using subscription mode: {llm.is_subscription}")
print(f"Model: {llm.model}")
print(f"Auth method: {auth_method}")

if os.getenv("SUBSCRIPTION_LOGIN_ONLY") == "1":
    print("Login verified; skipping agent run because SUBSCRIPTION_LOGIN_ONLY=1.")
    raise SystemExit(0)

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
