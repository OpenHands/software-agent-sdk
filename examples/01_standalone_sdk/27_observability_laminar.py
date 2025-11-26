"""
Observability & Laminar example

This example demonstrates enabling OpenTelemetry tracing with Laminar in the
OpenHands SDK. Set LMNR_PROJECT_API_KEY and run the script to see traces.
"""

import os

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.terminal import TerminalTool


# Tip: Set LMNR_PROJECT_API_KEY in your environment before running, e.g.:
#   export LMNR_PROJECT_API_KEY="your-laminar-api-key"
# For non-Laminar OTLP backends, set OTEL_* variables instead.

# Configure LLM and Agent
api_key = os.getenv("LLM_API_KEY")
model = os.getenv("LLM_MODEL", "openhands/claude-sonnet-4-5-20250929")
base_url = os.getenv("LLM_BASE_URL")
llm = LLM(
    model=model,
    api_key=SecretStr(api_key) if api_key else None,
    base_url=base_url,
    usage_id="agent",
)

agent = Agent(
    llm=llm,
    tools=[Tool(name=TerminalTool.name)],
)

# Create conversation and run a simple task
conversation = Conversation(agent=agent, workspace=".")
conversation.send_message("List the files in the current directory and print them.")
conversation.run()
print(
    "All done! Check your Laminar dashboard for traces "
    "(session is the conversation UUID)."
)
