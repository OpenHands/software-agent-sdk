"""Runnable proof that the ``/goal`` loop writes into the SAME conversation history.

What it does:
  1. Sends a normal "main conversation" message and runs the agent.
  2. Runs a ``/goal`` loop on the *same* ``Conversation`` object.
  3. Prints the single shared event log and checks that the main-conversation
     events are still there, untouched, with the goal's objective / agent work /
     judge-driven followups / completion appended after them.

The point: ``run_goal`` drives the conversation you pass in (it does not fork or
spin up a sidecar), so everything lands in one ``conversation.state.events`` log
under one ``conversation.id``. The agent-server ``EventService.start_goal`` uses
the same mechanism on its single ``_conversation``, so this proves the property
both paths rely on.

Run it two ways:
  # Deterministic, no network (scripted TestLLMs) -- always works, quick check:
  uv run python .pr/goal_shared_history.py

  # Real agent doing real work (creates files, runs pytest) -- opt in explicitly:
  GOAL_DEMO_REAL=1 LLM_API_KEY=sk-... LLM_MODEL=gpt-5.5 \
      uv run python .pr/goal_shared_history.py
"""

import os
import tempfile

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.conversation.goal import run_goal
from openhands.sdk.conversation.visualizer import DefaultConversationVisualizer
from openhands.sdk.event import LLMConvertibleEvent
from openhands.sdk.llm import Message, TextContent, content_to_str
from openhands.sdk.testing import TestLLM
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


def dump_history(conversation, title: str) -> list:
    """Print the conversation's full event log and return its events."""
    events = list(conversation.state.events)
    print(f"\n===== {title} =====")
    print(f"conversation id : {conversation.id}")
    print(f"total events    : {len(events)}")
    for i, ev in enumerate(events):
        if isinstance(ev, LLMConvertibleEvent):
            text = " ".join(content_to_str(ev.to_llm_message().content))
            text = text.strip().replace("\n", " ")
            print(f"  [{i:>2}] {ev.to_llm_message().role:<9} {text[:96]}")
        else:
            print(f"  [{i:>2}] {type(ev).__name__}")
    return events


def _scripted(*texts: str, usage_id: str) -> TestLLM:
    return TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text=t)]) for t in texts],
        usage_id=usage_id,
    )


def build(real: bool):
    """Return (agent, judge_llm, main_message, objective, max_iterations)."""
    if real:
        llm = LLM(
            usage_id="agent",
            model=os.getenv("LLM_MODEL", "gpt-5.5"),
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL"),
        )
        agent = Agent(
            llm=llm,
            tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)],
        )
        judge_llm = llm.model_copy(update={"usage_id": "goal-judge"})
        objective = (
            "Create mathx.py with an add(a, b) function and test_mathx.py with a "
            "pytest test for it. The goal is complete only when `python -m pytest "
            "-q` passes. Finish each turn with the finish tool."
        )
        return (
            agent,
            judge_llm,
            "Say hello and tell me which directory you are in.",
            objective,
            5,
        )

    # Deterministic path: scripted agent (one content-only reply per run) + a
    # judge that says "not done" once, then "done".
    agent = Agent(
        llm=_scripted(
            "Hello! I am working in the demo workspace.",  # main turn
            "I drafted mathx.py and a pytest for it.",  # goal round 1
            "Fixed it -- mathx.py and test_mathx.py now pass.",  # goal round 2
            usage_id="agent",
        ),
        tools=[],
    )
    judge_llm = _scripted(
        '{"score": 0.3, "complete": false, "missing": "tests not passing yet"}',
        '{"score": 1.0, "complete": true, "missing": ""}',
        usage_id="goal-judge",
    )
    return agent, judge_llm, "Say hello.", "Make `pytest` pass for mathx.py.", 5


def main() -> None:
    # Real mode is explicit opt-in so the deterministic demo always works,
    # even when a (possibly stale) LLM_API_KEY is present in the environment.
    real = os.getenv("GOAL_DEMO_REAL") == "1"
    print(f"mode: {'REAL LLM' if real else 'DETERMINISTIC (scripted TestLLM)'}")

    agent, judge_llm, main_message, objective, max_iters = build(real)
    workspace = tempfile.mkdtemp(prefix="goal-demo-")
    # visualizer=None keeps the output focused on the proof below.
    from pathlib import Path

    conversation = Conversation(
        agent=agent, workspace=workspace, visualizer=None, persistence_dir=Path.cwd()
    )
    convo_id = conversation.id

    # 1) A normal "main conversation" turn.
    conversation.send_message(main_message)
    conversation.run()
    main_events = dump_history(conversation, "AFTER MAIN CONVERSATION TURN")
    main_ids = [ev.id for ev in main_events]

    # 2) A /goal loop on the SAME conversation object.
    print(f"\n>>> running /goal: {objective}\n")
    outcome = run_goal(conversation, objective, judge_llm, max_iterations=max_iters)

    all_events = dump_history(conversation, "AFTER /goal LOOP (SAME CONVERSATION)")
    all_ids = [ev.id for ev in all_events]

    # 3) Prove it is one shared history.
    objective_in_log = any(
        objective[:20] in " ".join(content_to_str(ev.to_llm_message().content))
        for ev in all_events
        if isinstance(ev, LLMConvertibleEvent)
    )
    print("\n===== PROOF (shared history) =====")
    print(f"same conversation id .............. {conversation.id == convo_id}")
    print("only one Conversation object ...... True (no fork was created)")
    print(f"event log GREW in place ........... {len(main_ids)} -> {len(all_ids)}")
    print(f"main-convo events still present ... {all_ids[: len(main_ids)] == main_ids}")
    print(f"goal objective is in THIS log ..... {objective_in_log}")
    print(
        f"goal outcome ...................... {outcome.status} "
        f"(after {outcome.iterations} round(s))"
    )
    print(f"\nworkspace: {workspace}")

    # Visualize the whole thing AFTER the fact. Because every turn (main + goal)
    # is persisted in conversation.state.events, we can replay the conversation
    # through the SDK's visualizer at any time -- here, after the run finished.
    # (For LIVE output instead, drop `visualizer=None` above; the default
    # DefaultConversationVisualizer then prints each event as it happens.)
    print("\n===== REPLAY (visualizing the saved conversation) =====")
    visualizer = DefaultConversationVisualizer()
    for event in conversation.state.events:
        visualizer.on_event(event)


if __name__ == "__main__":
    main()
