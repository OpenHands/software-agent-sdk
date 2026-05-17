"""
Async LLM cancellation — instant interrupt of in-flight completions.

Demonstrates that with async, we can cancel an LLM call *while the model is
still generating tokens*.  The cancellation is instant because the event loop
regains control at the ``await`` boundary inside ``_atransport_call`` (or the
async-for that consumes the stream).

Contrast with the synchronous path where ``pause()`` cannot take effect until
the current blocking HTTP call finishes — see the ``pause()`` docstring:

    "If called during an LLM completion, the pause will not take effect
     until the current LLM call completes."

With ``arun()`` + ``task.cancel()``, there is no such limitation.

Usage:
    LLM_API_KEY=... python examples/01_standalone_sdk/50_async_cancellation.py

    # Press Ctrl-C at any time to cancel, or wait for the auto-cancel timer.
"""

import asyncio
import os
import signal
import sys
import time

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.llm.streaming import ModelResponseStream
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


# ── LLM (streaming enabled so we see tokens arrive) ─────────────────
api_key = os.getenv("LLM_API_KEY")
assert api_key, "Set LLM_API_KEY in your environment."

model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
base_url = os.getenv("LLM_BASE_URL")

llm = LLM(
    model=model,
    api_key=SecretStr(api_key),
    base_url=base_url,
    usage_id="async-cancel-demo",
    stream=True,
)

# ── Agent & conversation ─────────────────────────────────────────────
agent = Agent(
    llm=llm,
    tools=[
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
    ],
)

token_count = 0


def on_token(chunk: ModelResponseStream) -> None:
    """Print streaming tokens and count them."""
    global token_count
    for choice in chunk.choices:
        delta = choice.delta
        if delta is not None:
            text = getattr(delta, "content", None) or ""
            reasoning = getattr(delta, "reasoning_content", None) or ""
            fragment = text or reasoning
            if fragment:
                token_count += 1
                sys.stdout.write(fragment)
                sys.stdout.flush()


conversation = Conversation(
    agent=agent,
    workspace=os.getcwd(),
    token_callbacks=[on_token],
)

# Give the model a task that requires extended reasoning and generation
# so the LLM call is visibly in-flight when we cancel.
conversation.send_message(
    "Write a thorough, multi-page technical deep-dive (at least 2000 words) "
    "comparing every major sorting algorithm: bubble sort, selection sort, "
    "insertion sort, merge sort, quick sort, heap sort, radix sort, counting "
    "sort, Tim sort, and shell sort.  For each algorithm, include:\n"
    "  1. Pseudocode\n"
    "  2. Time complexity analysis (best / average / worst)\n"
    "  3. Space complexity\n"
    "  4. When you would choose it in practice\n"
    "  5. A worked example with the input [38, 27, 43, 3, 9, 82, 10]\n\n"
    "Write the full analysis into a file called sorting_deep_dive.md."
)


# ── Cancellation machinery ───────────────────────────────────────────
AUTO_CANCEL_SECONDS = float(os.getenv("AUTO_CANCEL_SECONDS", "5"))

# Global handle so SIGINT and the timer can both reach it
run_task: asyncio.Task[None] | None = None


def _request_cancel(source: str) -> None:
    """Cancel the arun() task from any context."""
    if run_task and not run_task.done():
        print(f"\n\n{'=' * 60}")
        print(f"⚡ Cancel requested ({source})")
        print(f"   Tokens received before cancel: {token_count}")
        print(f"{'=' * 60}\n")
        run_task.cancel()


async def _auto_cancel_timer() -> None:
    """Fire after AUTO_CANCEL_SECONDS to show programmatic cancellation."""
    await asyncio.sleep(AUTO_CANCEL_SECONDS)
    _request_cancel(f"auto-timer after {AUTO_CANCEL_SECONDS}s")


async def main() -> None:
    global run_task

    loop = asyncio.get_running_loop()

    # Wire Ctrl-C to cancel the task instead of killing the process.
    # On non-Unix (Windows) this gracefully degrades to KeyboardInterrupt.
    try:
        loop.add_signal_handler(
            signal.SIGINT,
            lambda: _request_cancel("Ctrl-C"),
        )
    except NotImplementedError:
        pass  # Windows — KeyboardInterrupt will still work

    print("=" * 60)
    print("Async LLM Cancellation Demo")
    print("=" * 60)
    print(f"Model          : {model}")
    print(f"Auto-cancel in : {AUTO_CANCEL_SECONDS}s  (or press Ctrl-C)")
    print("=" * 60)
    print()

    # ── Launch arun() as a cancellable task ──────────────────────────
    run_task = asyncio.create_task(conversation.arun())
    timer_task = asyncio.create_task(_auto_cancel_timer())

    wall_start = time.monotonic()

    try:
        await run_task
        # If we get here the LLM finished before the cancel fired
        timer_task.cancel()
        print("\n\nAgent finished before cancel timer fired.")
    except asyncio.CancelledError:
        cancel_latency_ms = (time.monotonic() - wall_start) * 1000
        timer_task.cancel()

        print()
        print("─" * 60)
        print("✅ arun() cancelled successfully!")
        print(f"   Wall time until cancel took effect : {cancel_latency_ms:.0f} ms")
        print(f"   Tokens streamed before cancellation: {token_count}")
        print(
            f"   Conversation status                : "
            f"{conversation.state.execution_status}"
        )
        print()
        print("With the synchronous path, pause() would have had to wait")
        print("for the entire LLM HTTP response to finish before it could")
        print("take effect.  With async, the cancellation interrupted the")
        print("in-flight request at the next `await` boundary — instantly.")
        print("─" * 60)

    # Report cost (partial — only tokens consumed before cancel)
    cost = llm.metrics.accumulated_cost
    print(f"\nEXAMPLE_COST: {cost}")


asyncio.run(main())
