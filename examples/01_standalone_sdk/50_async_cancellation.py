"""
Async interrupt demo — cancel during LLM reasoning *or* tool execution.

The task is designed to give two windows where ``interrupt()`` can fire:

1. **During LLM completion** — the model must reason about how to write
   the script and then run it, which takes several seconds of generation.
2. **During a long-running terminal command** — the agent runs a shell
   script that sleeps for 30 seconds, giving plenty of time to interrupt
   while a tool call is in-flight.

Press **Ctrl-C** at any point, or let the auto-timer fire after a
configurable delay (default 8 s).  The conversation transitions to
``PAUSED`` and can be resumed later with another ``arun()`` / ``run()``.

Usage:
    LLM_API_KEY=... python examples/01_standalone_sdk/50_async_cancellation.py

    # Override the auto-interrupt delay:
    AUTO_CANCEL_SECONDS=12 LLM_API_KEY=... python examples/...
"""

import asyncio
import os
import signal
import time

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, Event
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


# ── Configuration ────────────────────────────────────────────────────
api_key = os.getenv("LLM_API_KEY")
assert api_key, "Set LLM_API_KEY in your environment."

model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
base_url = os.getenv("LLM_BASE_URL")

llm = LLM(
    model=model,
    api_key=SecretStr(api_key),
    base_url=base_url,
    usage_id="async-cancel-demo",
)

# ── Agent & conversation ─────────────────────────────────────────────
agent = Agent(
    llm=llm,
    tools=[
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
    ],
)

# ── Live event log ───────────────────────────────────────────────────
phase = "llm"  # tracks which phase we're in for the status line


def on_event(event: Event) -> None:
    """Print a one-liner for each event so the user can see progress."""
    global phase
    if isinstance(event, ActionEvent):
        phase = "tool"
        print(f"  🔧 Agent calls: {event.tool_name}")
    elif isinstance(event, ObservationEvent):
        text = str(event.observation)[:120] if event.observation else ""
        print(f"  📋 Result: {text}")


conversation = Conversation(
    agent=agent,
    workspace=os.getcwd(),
    callbacks=[on_event],
)

# Ask the agent to do something that naturally produces TWO blocking
# phases: first a long LLM completion (reasoning + generation), then
# a long-running terminal command (sleep 30).
conversation.send_message(
    "Please do the following:\n"
    "1. Write a bash script called countdown.sh that prints the numbers "
    "30 down to 1, sleeping 1 second between each number.\n"
    "2. Run the script with `bash countdown.sh` and wait for it to finish.\n"
    "3. After the script completes, create a file called done.txt with "
    "the text 'Countdown complete!'."
)


# ── Interruption machinery ───────────────────────────────────────────
AUTO_CANCEL_SECONDS = float(os.getenv("AUTO_CANCEL_SECONDS", "8"))


def _request_interrupt(source: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"⚡ Interrupt requested ({source})")
    print(f"   Current phase: {phase}")
    print(f"{'=' * 60}\n")
    conversation.interrupt()


async def _auto_interrupt_timer() -> None:
    await asyncio.sleep(AUTO_CANCEL_SECONDS)
    _request_interrupt(f"auto-timer after {AUTO_CANCEL_SECONDS}s")


async def main() -> None:
    loop = asyncio.get_running_loop()

    # Wire Ctrl-C to interrupt instead of killing the process.
    try:
        loop.add_signal_handler(
            signal.SIGINT,
            lambda: _request_interrupt("Ctrl-C"),
        )
    except NotImplementedError:
        pass  # Windows — KeyboardInterrupt will still work

    print("=" * 60)
    print("Async Interrupt Demo")
    print("=" * 60)
    print(f"Model          : {model}")
    print(f"Auto-interrupt : {AUTO_CANCEL_SECONDS}s  (or press Ctrl-C)")
    print()
    print("The agent will first reason + generate code (window 1),")
    print("then run a 30-second countdown script (window 2).")
    print("Interrupt at any time to cancel instantly.")
    print("=" * 60)
    print()

    # ── Run with interrupt timer ─────────────────────────────────────
    timer_task = asyncio.create_task(_auto_interrupt_timer())
    wall_start = time.monotonic()

    await conversation.arun()

    timer_task.cancel()
    elapsed = time.monotonic() - wall_start

    # ── Summary ──────────────────────────────────────────────────────
    status = conversation.state.execution_status
    print()
    print("─" * 60)
    print(f"Status    : {status}")
    print(f"Wall time : {elapsed:.1f}s")
    print(f"Phase     : interrupted during {phase}")
    print()
    if str(status) == "paused":
        print("The conversation is paused — you could resume it with")
        print("another arun() or run() call to let the agent continue.")
    print("─" * 60)

    cost = llm.metrics.accumulated_cost
    print(f"\nEXAMPLE_COST: {cost}")


asyncio.run(main())
