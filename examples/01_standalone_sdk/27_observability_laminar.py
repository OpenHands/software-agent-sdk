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
llm_api_key = os.getenv("LLM_API_KEY")
llm = LLM(
    model="anthropic/claude-sonnet-4-5-20250929",
    api_key=SecretStr(llm_api_key) if llm_api_key else None,
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
    "All done! Check your Laminar dashboard for traces (session is the conversation UUID)."
)
