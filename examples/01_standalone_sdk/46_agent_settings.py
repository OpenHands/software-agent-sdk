"""Create, serialize, and deserialize OpenHandsAgentSettings, then build a working agent.

Demonstrates:
1. Configuring an agent entirely through OpenHandsAgentSettings (LLM, tools, condenser).
2. Serializing settings to JSON and restoring them.
3. Building an Agent from settings via ``create_agent()``.
4. Running a short conversation to prove the settings take effect.
5. Changing the tool list and showing the agent's capabilities change.
"""

import json
import os

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation, OpenHandsAgentSettings, Tool
from openhands.sdk.settings import CondenserSettings
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


# ── 1. Build settings ────────────────────────────────────────────────────
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."

settings = OpenHandsAgentSettings(
    llm=LLM(
        model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
        api_key=SecretStr(api_key),
        base_url=os.getenv("LLM_BASE_URL"),
    ),
    tools=[
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
    ],
    condenser=CondenserSettings(enabled=True, max_size=50),
)

# ── 2. Serialize → JSON → deserialize ────────────────────────────────────
payload = settings.model_dump(mode="json")
print("Serialized settings (JSON):")
print(json.dumps(payload, indent=2, default=str)[:800], "…")
print()

restored = OpenHandsAgentSettings.model_validate(payload)
assert restored.condenser.enabled is True
assert restored.condenser.max_size == 50
assert len(restored.tools) == 2
print("✓ Roundtrip deserialization successful — all fields preserved")
print()

# ── 3. Create agent from settings and run a task ─────────────────────────
agent = settings.create_agent()
print(f"Agent created: llm.model={agent.llm.model}")
print(f"  tools={[t.name for t in agent.tools]}")
print(f"  condenser={type(agent.condenser).__name__}")
print()

cwd = os.getcwd()
conversation = Conversation(agent=agent, workspace=cwd)
conversation.send_message(
    "Create a file called hello_settings.txt containing "
    "'Agent settings work!' then confirm the file exists with ls."
)
conversation.run()

# Verify the agent actually wrote the file
assert os.path.exists(os.path.join(cwd, "hello_settings.txt")), (
    "Agent should have created hello_settings.txt"
)
print("✓ Agent created hello_settings.txt — settings drove real behavior")
print()

# ── 4. Different settings → different behavior ───────────────────────────
# Now create settings with ONLY the terminal tool and condenser disabled.
terminal_only_settings = OpenHandsAgentSettings(
    llm=settings.llm,
    tools=[Tool(name=TerminalTool.name)],
    condenser=CondenserSettings(enabled=False),
)

terminal_agent = terminal_only_settings.create_agent()
print(f"Terminal-only agent tools: {[t.name for t in terminal_agent.tools]}")
assert len(terminal_agent.tools) == 1
assert terminal_agent.condenser is None  # condenser disabled in these settings
print("✓ Different settings produce different agent configuration")
print()

# ── Cleanup ──────────────────────────────────────────────────────────────
os.remove(os.path.join(cwd, "hello_settings.txt"))

# Report cost
cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"\nEXAMPLE_COST: {cost}")
