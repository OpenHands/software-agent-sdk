"""Client-defined tools via the agent-server API.

Demonstrates the server-side path for client-defined tools: the agent runs
inside a local ``openhands.agent_server`` subprocess, tool specs are sent in
``POST /api/conversations``, and the SDK broadcasts ``ActionEvent`` s over
WebSocket so this client process can intercept them — exactly what a browser
frontend (e.g. Agent Canvas) would do.

Differences from the standalone SDK example:
  - No local tool registration: the server calls ``ClientTool.from_spec()`` and
    injects ``Tool(name=...)`` into the agent automatically.
  - ``ActionEvent`` s arrive asynchronously via WebSocket through callbacks.
  - ``client_tools=SPECS`` is the only new parameter on ``Conversation``.

Usage:
    LLM_API_KEY=... uv run examples/02_remote_agent_server/14_client_defined_tools.py
"""

import os
import tempfile

from pydantic import SecretStr
from scripts.utils import ManagedAPIServer

from openhands.sdk import LLM, Conversation, Event, RemoteConversation, get_logger
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.tool.client_tool import ClientToolSpec
from openhands.tools.preset.default import get_default_agent


logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 1. Define tools via JSON spec — no Python executor, no server-side code
# ---------------------------------------------------------------------------

SPECS = [
    ClientToolSpec(
        name="show_notification",
        description=(
            "Display a notification banner in the UI. "
            "Use this to surface important status updates to the user."
        ),
        parameters={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Text to display in the notification",
                },
                "level": {
                    "type": "string",
                    "enum": ["info", "warning", "error"],
                    "description": "Severity level of the notification",
                },
            },
            "required": ["message", "level"],
        },
    ),
    ClientToolSpec(
        name="navigate_to",
        description=(
            "Navigate the UI to a specific page or route. "
            "Use this to direct the user to a relevant section."
        ),
        parameters={
            "type": "object",
            "properties": {
                "route": {
                    "type": "string",
                    "description": "Route or URL path to navigate to, e.g. '/files'",
                },
            },
            "required": ["route"],
        },
    ),
]

CLIENT_TOOL_NAMES = {spec.name for spec in SPECS}

# ---------------------------------------------------------------------------
# 2. Event callback: intercept client tool calls delivered over WebSocket
#    In a real browser client this would forward the call to the UI layer.
# ---------------------------------------------------------------------------

intercepted: list[dict] = []


def on_event(event: Event) -> None:
    if isinstance(event, ActionEvent) and event.tool_name in CLIENT_TOOL_NAMES:
        args = event.action.model_dump() if event.action else {}
        intercepted.append({"tool": event.tool_name, "args": args})
        logger.info("[CLIENT HANDLER] %s(%s)", event.tool_name, args)


# ---------------------------------------------------------------------------
# 3. Build LLM — the agent runs on the server, this config is serialised there
# ---------------------------------------------------------------------------

api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."

llm = LLM(
    model=os.getenv("LLM_MODEL", "gpt-5.5"),
    api_key=SecretStr(api_key),
    base_url=os.getenv("LLM_BASE_URL"),
)

# ---------------------------------------------------------------------------
# 4. Start a local agent-server, connect, and run the conversation
# ---------------------------------------------------------------------------

with ManagedAPIServer(port=8001) as server:
    workspace_dir = tempfile.mkdtemp(prefix="client_tools_demo_")

    from openhands.sdk import Workspace

    workspace = Workspace(host=server.base_url, working_dir=workspace_dir)

    # The agent does NOT need to list client tools — the server injects them
    # automatically when it processes the client_tools field in the request.
    agent = get_default_agent(llm=llm, cli_mode=True)

    conversation = Conversation(
        agent=agent,
        workspace=workspace,
        callbacks=[on_event],
        client_tools=SPECS,  # sent to server in POST /api/conversations
    )
    assert isinstance(conversation, RemoteConversation)

    try:
        logger.info("Conversation ID: %s", conversation.state.id)

        conversation.send_message(
            "Use the terminal to count how many Python files are in the current "
            "directory (use: find . -name '*.py' | wc -l). "
            "Then call show_notification with the count result and level='info', "
            "and call navigate_to with route='/files'."
        )
        conversation.run()

        # -----------------------------------------------------------------------
        # 5. Verify that the client tools were actually called over WebSocket
        # -----------------------------------------------------------------------

        logger.info("\n=== %d client tool call(s) intercepted ===", len(intercepted))
        for call in intercepted:
            logger.info("  %s: %s", call["tool"], call["args"])

        called_names = {c["tool"] for c in intercepted}
        assert "show_notification" in called_names, (
            "Expected show_notification to be called"
        )
        assert "navigate_to" in called_names, "Expected navigate_to to be called"

        cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
        print(f"EXAMPLE_COST: {cost}")

    finally:
        conversation.close()
