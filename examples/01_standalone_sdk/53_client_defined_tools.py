"""Demonstrate client-defined tools — tools registered via JSON spec that are
executed by an external client (e.g. a browser frontend), not the SDK itself.

This pattern lets frontend applications register "UI action" tools without any
Python code on the server side. When the agent calls such a tool, the SDK returns
an acknowledgment observation immediately and the event callback (or WebSocket
client) can handle the actual execution.

Usage:
    LLM_API_KEY=... uv run python examples/01_standalone_sdk/53_client_defined_tools.py
"""

import os

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, Event, Tool
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.tool import ClientToolSpec
from openhands.tools.terminal import TerminalTool


# ---------------------------------------------------------------------------
# 1. Define tools via JSON spec — no Python executor required
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
                    "description": "Route or URL path to navigate to, e.g. '/settings'",
                },
            },
            "required": ["route"],
        },
    ),
]

CLIENT_TOOL_NAMES: set[str] = {spec.name for spec in SPECS}

# ---------------------------------------------------------------------------
# 2. Capture client tool invocations via event callback
#    In a real application this callback would forward the call to the frontend
#    over WebSocket; here we just record the calls for verification.
# ---------------------------------------------------------------------------

intercepted: list[dict] = []


def on_event(event: Event) -> None:
    if isinstance(event, ActionEvent) and event.tool_name in CLIENT_TOOL_NAMES:
        args = event.action.model_dump() if event.action else {}
        intercepted.append({"tool": event.tool_name, "args": args})
        print(f"[CLIENT HANDLER] {event.tool_name}({args})")


# ---------------------------------------------------------------------------
# 3. Build agent with standard tools, then pass the client tools straight to
#    Conversation. The SDK registers them and injects them into the agent — no
#    manual register_tool / Tool(name=...) wiring required.
# ---------------------------------------------------------------------------

api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."

llm = LLM(
    model=os.getenv("LLM_MODEL", "gpt-5.5"),
    api_key=SecretStr(api_key),
    base_url=os.getenv("LLM_BASE_URL"),
)

agent = Agent(
    llm=llm,
    tools=[Tool(name=TerminalTool.name)],
)

conversation = Conversation(
    agent=agent,
    workspace=os.getcwd(),
    callbacks=[on_event],
    client_tools=SPECS,
)

conversation.send_message(
    "Use the terminal to count how many Python files are in the current directory "
    "(use: find . -name '*.py' | wc -l). "
    "Then call show_notification with the count result and level='info', "
    "and call navigate_to with route='/files'."
)
conversation.run()

# ---------------------------------------------------------------------------
# 4. Verify that the client tools were actually called
# ---------------------------------------------------------------------------

print(f"\n=== {len(intercepted)} client tool call(s) intercepted ===")
for call in intercepted:
    print(f"  {call['tool']}: {call['args']}")

called_names = {c["tool"] for c in intercepted}
assert "show_notification" in called_names, "Expected show_notification to be called"
assert "navigate_to" in called_names, "Expected navigate_to to be called"

print(f"EXAMPLE_COST: {llm.metrics.accumulated_cost}")
