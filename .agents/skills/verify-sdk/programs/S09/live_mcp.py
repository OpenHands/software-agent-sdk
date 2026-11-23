"""A real model uses tools from a local stdio MCP server.

The agent has only the MCP tools of mcp_server.py (plus the built-ins); the
task needs the server's `add` and `reverse` tools and a conversation secret.
"""

import sys
from pathlib import Path

from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, Conversation
from openhands.sdk.event import ObservationEvent
from openhands.sdk.mcp import MCPServer


server = str(Path(__file__).with_name("mcp_server.py"))
agent = Agent(
    llm=make_llm(),
    tools=[],
    mcp_config={"verify": MCPServer(command=sys.executable, args=[server, "stdio"])},
)
conversation = Conversation(
    agent=agent,
    workspace=str(run_paths()["workspace"]),
    secrets={"PASSPHRASE": "open-sesame"},
    visualizer=None,
)
conversation.send_message(
    "Use the add tool to add 17 and 25. Then call the reverse tool with the text "
    "$PASSPHRASE exactly as written (it is a secret reference). Then finish and "
    "report the sum."
)
conversation.run()
observations = [
    (e.tool_name, e.observation.text, e.observation.is_error)
    for e in conversation.state.events
    if isinstance(e, ObservationEvent)
]
result = {
    "ok": True,
    "status": conversation.state.execution_status.value,
    "tools_called": sorted({name for name, _, _ in observations}),
    "add_result_42": any(
        name == "add" and "42" in text for name, text, _ in observations
    ),
    "reverse_expanded": any(
        name == "reverse" and "emases-nepo" in text for name, text, _ in observations
    ),
    "mcp_errors": [name for name, _, err in observations if err],
}
conversation.close()
emit(result)
