"""Opt-in persistent memory across sessions (two-tier ``MEMORY.md``).

With ``AgentContext(load_memory=True)`` a conversation loads the ``MEMORY.md``
indexes from ``~/.openhands/memory/`` (user tier) and
``<workspace>/.openhands/memory/`` (project tier) into the system prompt at
session start (the ``<MEMORY_CONTEXT>`` block), and the system prompt
instructs the agent to maintain those files as it works.

This example runs two conversations over the same workspace:

1. Session 1 asks the agent to record a project decision in its persistent
   project memory -- the agent writes ``.openhands/memory/MEMORY.md`` itself.
2. Session 2 is a brand-new conversation: the saved memory is injected into
   its system prompt automatically, so the agent already knows the decision
   without being told again.

Memory is opt-in and off by default. The example only writes inside a
temporary workspace; the user tier under ``~`` is left untouched.
"""

import os
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext, Conversation, get_logger
from openhands.sdk.event import SystemPromptEvent
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


logger = get_logger(__name__)

# Configure LLM
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
model = os.getenv("LLM_MODEL", "gpt-5.5")
base_url = os.getenv("LLM_BASE_URL")
llm = LLM(
    usage_id="agent",
    model=model,
    base_url=base_url,
    api_key=SecretStr(api_key),
)

tools = [Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)]

# Opt in to persistent memory. Everything else is automatic: the conversation
# resolves the MEMORY.md indexes at session start, and the system prompt tells
# the agent how to maintain them.
agent_context = AgentContext(load_memory=True)

with tempfile.TemporaryDirectory() as workspace:
    memory_index = Path(workspace) / ".openhands" / "memory" / "MEMORY.md"

    print("=" * 100)
    print("Session 1: ask the agent to record a decision in project memory.")
    agent = Agent(llm=llm, tools=tools, agent_context=agent_context)
    conversation = Conversation(agent=agent, workspace=workspace)
    conversation.send_message(
        "We just decided to use `uv` (not pip/poetry) for all Python "
        "dependency management in this project. Record that decision in your "
        "persistent project memory so future sessions know it."
    )
    conversation.run()
    conversation.close()

    print("=" * 100)
    print(f"Project memory after session 1 ({memory_index}):")
    if memory_index.exists():
        print(memory_index.read_text())
    else:
        print("(the agent did not create the memory index)")

    print("=" * 100)
    print("Session 2: a brand-new conversation over the same workspace.")
    agent = Agent(llm=llm, tools=tools, agent_context=agent_context)
    conversation = Conversation(agent=agent, workspace=workspace)
    conversation.send_message(
        "Which tool do we use for Python dependency management in this "
        "project? Answer from what you already know about the project."
    )
    conversation.run()

    # The recorded memory was injected into session 2's system prompt as the
    # <MEMORY_CONTEXT> block -- show it to make the mechanism visible.
    system_prompt_event = next(
        event
        for event in conversation.state.events
        if isinstance(event, SystemPromptEvent)
    )
    dynamic_context = system_prompt_event.dynamic_context
    injected = dynamic_context.text if dynamic_context else ""
    print("=" * 100)
    print(
        "<MEMORY_CONTEXT> injected into session 2's system prompt: "
        f"{'<MEMORY_CONTEXT>' in injected}"
    )
    conversation.close()

# Report cost
cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
