"""Fork a conversation through the agent server REST API.

Demonstrates ``RemoteConversation.fork()`` which delegates to the server's
``POST /api/conversations/{id}/fork`` endpoint.  The fork deep-copies
events and state on the server side, then returns a new
``RemoteConversation`` pointing at the copy.

Scenarios covered:
  1. Run a source conversation on the server
  2. Fork it — verify independent event histories
  3. Fork with a title and custom tags
"""

import os
import tempfile

from _utils import ManagedAPIServer
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, RemoteConversation, Tool, Workspace
from openhands.tools.terminal import TerminalTool


# -----------------------------------------------------------------
# Config
# -----------------------------------------------------------------
api_key = os.getenv("LLM_API_KEY")
assert api_key, "LLM_API_KEY must be set"

llm = LLM(
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    api_key=SecretStr(api_key),
    base_url=os.getenv("LLM_BASE_URL"),
)
agent = Agent(llm=llm, tools=[Tool(name=TerminalTool.name)])

# -----------------------------------------------------------------
# Run
# -----------------------------------------------------------------
with ManagedAPIServer(port=8002) as server:
    workspace_dir = tempfile.mkdtemp(prefix="fork_demo_")
    workspace = Workspace(host=server.base_url, working_dir=workspace_dir)

    # =============================================================
    # 1. Source conversation
    # =============================================================
    source = Conversation(agent=agent, workspace=workspace)
    assert isinstance(source, RemoteConversation)

    source.send_message("Run `echo hello-from-source` in the terminal.")
    source.run()

    print("=" * 64)
    print("  RemoteConversation.fork() — Agent-Server Example")
    print("=" * 64)
    print(f"\nSource conversation ID : {source.id}")
    source_event_count = len(source.state.events)
    print(f"Source events count    : {source_event_count}")

    # =============================================================
    # 2. Fork and continue independently
    # =============================================================
    fork = source.fork(title="Follow-up fork")
    assert isinstance(fork, RemoteConversation)

    print("\n--- Fork created ---")
    print(f"Fork ID                : {fork.id}")
    fork_event_count = len(fork.state.events)
    print(f"Fork events (copied)   : {fork_event_count}")

    assert fork.id != source.id
    # The fork copies all persisted events from the server-side EventLog.
    # The source's client-side list may additionally contain transient
    # WebSocket-only events (e.g. full-state snapshots) that are never
    # persisted, so we only assert the fork has a non-trivial number of
    # events rather than exact parity.
    assert fork_event_count > 0

    fork.send_message("Now run `echo hello-from-fork` in the terminal.")
    fork.run()

    print("\n--- After running fork ---")
    print(f"Source events          : {len(source.state.events)}")
    print(f"Fork events (grew)     : {len(fork.state.events)}")
    assert len(fork.state.events) > fork_event_count

    # =============================================================
    # 3. Fork with tags
    # =============================================================
    fork_tagged = source.fork(
        title="Tagged experiment",
        tags={"purpose": "a/b-test"},
    )
    assert isinstance(fork_tagged, RemoteConversation)

    print("\n--- Fork with tags ---")
    print(f"Fork ID     : {fork_tagged.id}")

    fork_tagged.send_message(
        "What command did you run earlier? Just tell me, no tools."
    )
    fork_tagged.run()

    print(f"Fork events : {len(fork_tagged.state.events)}")

    # =============================================================
    # Summary
    # =============================================================
    print(f"\n{'=' * 64}")
    print("All done — RemoteConversation.fork() works end-to-end.")
    print("=" * 64)

    # Cleanup
    fork.close()
    fork_tagged.close()
    source.close()

cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
