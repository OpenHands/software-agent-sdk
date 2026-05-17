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

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.terminal import TerminalTool


# -----------------------------------------------------------------
# Setup
# -----------------------------------------------------------------
llm = LLM(
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", None),
)

agent = Agent(llm=llm, tools=[Tool(name=TerminalTool.name)])
cwd = os.getcwd()

# =================================================================
# 1. Run the source conversation
# =================================================================
source = Conversation(agent=agent, workspace=cwd)
source.send_message("Run `echo hello-from-source` in the terminal.")
source.run()

print("=" * 64)
print("  Conversation.fork() — SDK Example")
print("=" * 64)
print(f"\nSource conversation ID : {source.id}")
print(f"Source events count    : {len(source.state.events)}")

# =================================================================
# 2. Fork and continue independently
# =================================================================
fork = source.fork(title="Follow-up fork")
source_event_count = len(source.state.events)

print("\n--- Fork created ---")
print(f"Fork ID                : {fork.id}")
print(f"Fork events (copied)   : {len(fork.state.events)}")
print(f"Fork title             : {fork.state.tags.get('title')}")

assert fork.id != source.id
assert len(fork.state.events) == source_event_count

fork.send_message("Now run `echo hello-from-fork` in the terminal.")
fork.run()

# Source is untouched
assert len(source.state.events) == source_event_count
print("\n--- After running fork ---")
print(f"Source events (unchanged): {source_event_count}")
print(f"Fork events (grew)       : {len(fork.state.events)}")

# =================================================================
# 3. Fork with a different agent (tool-change / A/B testing)
# =================================================================
alt_llm = LLM(
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", None),
    usage_id="alt",
)
alt_agent = Agent(llm=alt_llm, tools=[Tool(name=TerminalTool.name)])

fork_alt = source.fork(
    agent=alt_agent,
    title="Tool-change experiment",
    tags={"purpose": "a/b-test"},
)

print("\n--- Fork with alternate agent ---")
print(f"Fork ID     : {fork_alt.id}")
print(f"Fork tags   : {dict(fork_alt.state.tags)}")

fork_alt.send_message("What command did you run earlier? Just tell me, no tools.")
fork_alt.run()

print(f"Fork events : {len(fork_alt.state.events)}")

# =================================================================
# Summary
# =================================================================
print(f"\n{'=' * 64}")
print("All done — fork() works end-to-end.")
print("=" * 64)

# Report cost
cost = llm.metrics.accumulated_cost + alt_llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
