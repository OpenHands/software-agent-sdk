"""Branch a conversation. The conversation is a tree (every event has a
``parent_id``; the HEAD is ``leaf_event_id``), exposing three primitives:

  - ``fork()`` — deep-copy the whole conversation into a new one; ``run()``
    resumes with full event memory.
  - ``fork(from_event_id=...)`` — fork only the branch up to a chosen event;
    the fork's HEAD is that event.
  - ``navigate_to(event_id)`` — move HEAD within one conversation; appending
    after navigating creates a sibling branch, abandoned events stay on disk.

Use cases: debugging a wrong CI patch off the original's audit trail,
A/B-testing prompts, fork-on-tool-change, edit-a-past-turn-and-re-run.
"""

import os

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.terminal import TerminalTool


# -----------------------------------------------------------------
# Setup
# -----------------------------------------------------------------
llm = LLM(
    model=os.getenv("LLM_MODEL", "gpt-5.5"),
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
    model=os.getenv("LLM_MODEL", "gpt-5.5"),
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
# 4. Branch-slice fork: fork from a chosen past event
# =================================================================
# Fork from the first user message: copies only path_to_root(event) and sets
# the fork's HEAD there.
from openhands.sdk.event import MessageEvent  # noqa: E402


cut_event_id = next(
    e.id
    for e in source.state.events
    if isinstance(e, MessageEvent) and e.source == "user"
)

fork_slice = source.fork(from_event_id=cut_event_id, title="Branch from first turn")

print("\n--- Branch-slice fork (from_event_id) ---")
print(f"Cut event id          : {cut_event_id}")
print(f"Source events (full)  : {len(source.state.events)}")
print(f"Fork events (sliced)  : {len(fork_slice.state.events)}")
print(f"Fork HEAD             : {fork_slice.state.leaf_event_id}")

# The slice contains only the branch up to the cut point.
assert len(fork_slice.state.events) <= len(source.state.events)
assert fork_slice.state.leaf_event_id == cut_event_id

fork_slice.send_message("Run `echo sliced-branch` in the terminal.")
fork_slice.run()
print(f"Fork events after run : {len(fork_slice.state.events)}")
assert any("sliced-branch" in str(e) for e in fork_slice.state.events)

# =================================================================
# 5. In-conversation navigation: move HEAD and create a sibling branch
# =================================================================
# Move HEAD back to the cut point and send a new message: a sibling branch.
# The abandoned branch stays on disk but leaves the agent's active context.
events_before_nav = len(source.state.events)

source.navigate_to(cut_event_id)
print("\n--- navigate_to (in-conversation branching) ---")
print(f"HEAD moved to         : {source.state.leaf_event_id}")
print(f"Active branch length  : {len(source.state.view.events)} events in context")
print(f"Events still on disk  : {events_before_nav}")

source.send_message("Actually, instead run `echo sibling-branch`.")
source.run()
print(f"Events on disk now     : {len(source.state.events)} (old branch retained)")

# =================================================================
# Summary
# =================================================================
print(f"\n{'=' * 64}")
print("All done — fork() works end-to-end.")
print("=" * 64)

# Report cost
cost = llm.metrics.accumulated_cost + alt_llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
