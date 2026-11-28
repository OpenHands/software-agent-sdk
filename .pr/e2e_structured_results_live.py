"""Live e2e for MCP structured results.

A real model calls two tools on a real stdio MCP server
(.pr/e2e_structured_server.py):
- list_open_ports returns the structured result [22, 80, 443], a
  top-level JSON array (allowed by the 2026-07-28 protocol revision);
- feature_flags returns {"beta_enabled": true, "dark_mode": false}, and the
  conversation holds a secret whose value is "true".
Expected: the array survives as the observation's structured_content, and
the boolean that matches the secret is masked while the other is kept.
Run from the repo checkout:

    LLM_API_KEY=$(pass ai/zai) uv run python <this file>
"""

import os
import sys
import tempfile
from pathlib import Path

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.conversation.response_utils import get_agent_final_response
from openhands.sdk.mcp import MCPServer, MCPToolObservation


SERVER = Path(__file__).with_name("e2e_structured_server.py").resolve()

llm = LLM(
    model="openai/glm-4.7",
    api_key=os.environ["LLM_API_KEY"],
    base_url="https://api.z.ai/api/coding/paas/v4",
    usage_id="e2e-mcp-structured-results",
)

agent = Agent(
    llm=llm,
    tools=[],
    mcp_config={"e2e": MCPServer(command=sys.executable, args=[str(SERVER)])},
)

conv = Conversation(
    agent=agent,
    workspace=tempfile.mkdtemp(prefix="e2e_mcp_structured_"),
    visualizer=None,
)
conv.update_secrets({"BETA_FLAG": "true"})

conv.send_message(
    "Call the list_open_ports tool, then the feature_flags tool, once each. "
    "Then report what each one returned."
)
conv.run()

print("=== E2E RESULT ===")
for event in conv.state.events:
    observation = getattr(event, "observation", None)
    if isinstance(observation, MCPToolObservation):
        print(
            f"observation {observation.tool_name}: is_error={observation.is_error} "
            f"structured_content={observation.structured_content!r}"
        )
    elif observation is not None:
        print(f"other observation {type(observation).__name__}: {observation!r}"[:300])
    if type(event).__name__ in ("AgentErrorEvent", "ConversationErrorEvent"):
        print(f"{type(event).__name__}: {getattr(event, 'error', event)!r}"[:400])

print(f"final status: {conv.state.execution_status}")
print(f"final agent response: {get_agent_final_response(conv.state.events)}")
