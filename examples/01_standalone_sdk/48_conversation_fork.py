"""Fork a conversation to branch off for follow-up exploration.

``Conversation.fork()`` deep-copies a conversation — events, agent config,
workspace metadata — into a new conversation with its own ID.  The fork
starts in ``idle`` status and retains full event memory of the source, so
calling ``run()`` picks up right where the original left off.

Use cases:
  - CI agents that produced a wrong patch — engineer forks to debug
    without losing the original run's audit trail
  - A/B-testing prompts — fork at a given turn, change one variable,
    compare downstream
  - Swapping tools mid-conversation (fork-on-tool-change)
"""

import os

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.terminal import TerminalTool


api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."

llm = LLM(
    usage_id="agent",
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=SecretStr(api_key),
)

agent = Agent(llm=llm, tools=[Tool(name=TerminalTool.name)])

# --- 1. Run the source conversation ---
source = Conversation(agent=agent, workspace=os.getcwd())
source.send_message("Run `echo hello` in the terminal.")
source.run()

print(f"Source conversation ID : {source.id}")
print(f"Source events count    : {len(source.state.events)}")

# --- 2. Fork the conversation ---
fork = source.fork(title="Follow-up fork")

print(f"\nFork conversation ID   : {fork.id}")
print(f"Fork events count      : {len(fork.state.events)}")
print(f"Fork title tag         : {fork.state.tags.get('title')}")

# The fork has the same events — the agent remembers the full history.
assert fork.id != source.id
assert len(fork.state.events) == len(source.state.events)

# --- 3. Continue the fork independently ---
fork.send_message("Now run `echo world` in the terminal.")
fork.run()

# Source is untouched
print("\nAfter running fork:")
print(f"  Source events: {len(source.state.events)}")
print(f"  Fork events  : {len(fork.state.events)}")
assert len(fork.state.events) > len(source.state.events)

# --- 4. Fork with a different agent (tool-change scenario) ---
alt_agent = Agent(
    llm=LLM(
        usage_id="alt-agent",
        model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
        base_url=os.getenv("LLM_BASE_URL"),
        api_key=SecretStr(api_key),
    ),
    tools=[Tool(name=TerminalTool.name)],
)

fork_with_new_agent = source.fork(
    agent=alt_agent,
    title="Tool-change fork",
    tags={"purpose": "experiment"},
)
print(f"\nTool-change fork ID    : {fork_with_new_agent.id}")
print(f"  tags: {dict(fork_with_new_agent.state.tags)}")

# The fork uses the alt agent but retains the source's event history.
fork_with_new_agent.send_message("What command did you run earlier?")
fork_with_new_agent.run()

# Report cost
cost = llm.metrics.accumulated_cost + alt_agent.llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
