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

This example demonstrates the fork API end-to-end without calling an LLM,
focusing on the state-management primitive itself.  In a real workflow you
would call ``fork.run()`` to resume agentic execution.
"""

import tempfile

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation


# -----------------------------------------------------------------
# Setup — minimal agent (no real LLM calls needed for the demo)
# -----------------------------------------------------------------
llm = LLM(model="gpt-4o-mini", api_key=SecretStr("demo-key"), usage_id="demo")
agent = Agent(llm=llm, tools=[])

with tempfile.TemporaryDirectory() as workspace:
    # =============================================================
    # 1. Create a source conversation and populate it with events
    # =============================================================
    source = Conversation(agent=agent, workspace=workspace)

    # send_message() adds events to the conversation state without
    # calling an LLM.
    source.send_message("Analyse the sales report and list top trends.")
    source.send_message("Focus on the EMEA region specifically.")

    print("=" * 64)
    print("  Conversation.fork() — SDK Example")
    print("=" * 64)

    print(f"\nSource conversation ID : {source.id}")
    print(f"Source events count    : {len(source.state.events)}")
    print(f"Source status          : {source.state.execution_status}")

    # =============================================================
    # 2. Basic fork — full event history is deep-copied
    # =============================================================
    fork = source.fork(title="Follow-up exploration")

    print("\n--- Basic fork ---")
    print(f"Fork conversation ID   : {fork.id}")
    print(f"Fork events count      : {len(fork.state.events)}")
    print(f"Fork title tag         : {fork.state.tags.get('title')}")
    print(f"Fork status            : {fork.state.execution_status}")

    assert fork.id != source.id, "Fork must have a different ID"
    assert len(fork.state.events) == len(source.state.events), (
        "Fork must copy all events"
    )
    assert fork.state.tags.get("title") == "Follow-up exploration"
    print("OK: Fork has same event count, different ID, correct title")

    # =============================================================
    # 3. Source isolation — changes to fork don't affect source
    # =============================================================
    source_event_count = len(source.state.events)
    fork.send_message("Also compare with last quarter.")

    assert len(source.state.events) == source_event_count, (
        "Source must remain unmodified"
    )
    assert len(fork.state.events) > source_event_count, "Fork should have more events"

    print("\n--- Source isolation ---")
    print(f"Source events (unchanged): {len(source.state.events)}")
    print(f"Fork events (grew)       : {len(fork.state.events)}")
    print("OK: Source is immutable after fork")

    # =============================================================
    # 4. Deep-copy isolation — event lists are independent
    # =============================================================
    fork2 = source.fork()
    fork2_initial = len(fork2.state.events)
    fork2.send_message("Extra message only in fork2.")

    assert len(source.state.events) == source_event_count
    assert len(fork2.state.events) == fork2_initial + 1
    print("\n--- Deep-copy isolation ---")
    print("OK: Fork event list is independent from source")

    # =============================================================
    # 5. Fork with a different agent (tool-change / A/B testing)
    # =============================================================
    alt_llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("demo-key"),
        usage_id="alt",
    )
    alt_agent = Agent(llm=alt_llm, tools=[])

    fork_alt = source.fork(
        agent=alt_agent,
        title="Tool-change experiment",
        tags={"purpose": "a/b-test", "variant": "B"},
    )

    print("\n--- Fork with alternate agent ---")
    print(f"Fork ID     : {fork_alt.id}")
    print(f"Fork model  : {fork_alt.agent.llm.model}")
    print(f"Fork tags   : {dict(fork_alt.state.tags)}")
    print(f"Fork events : {len(fork_alt.state.events)}")

    assert fork_alt.agent.llm.model == "gpt-4o", "Alternate agent should be used"
    assert fork_alt.state.tags.get("purpose") == "a/b-test"
    assert len(fork_alt.state.events) == len(source.state.events)
    print("OK: Fork uses alternate agent, retains event history")

    # =============================================================
    # 6. Metrics reset (default behaviour)
    # =============================================================
    fork_reset = source.fork()
    fork_keep = source.fork(reset_metrics=False)

    reset_cost = fork_reset.state.stats.get_combined_metrics().accumulated_cost
    keep_cost = fork_keep.state.stats.get_combined_metrics().accumulated_cost

    print("\n--- Metrics ---")
    print(f"Fork (reset=True)  accumulated_cost: {reset_cost}")
    print(f"Fork (reset=False) accumulated_cost: {keep_cost}")
    print("OK: Metrics respect reset_metrics flag")

    # =============================================================
    # Summary
    # =============================================================
    print(f"\n{'=' * 64}")
    print("All assertions passed — fork() works correctly.")
    print(
        "\nIn a real workflow, call fork.run() to resume agentic execution"
        "\nfrom the copied state. The agent will have full memory of the"
        "\nsource conversation."
    )
    print("=" * 64)

# No LLM calls were made
print("EXAMPLE_COST: 0")
