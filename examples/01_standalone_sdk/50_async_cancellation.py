"""
Async LLM cancellation — instant interrupt of in-flight completions.

Demonstrates ``conversation.interrupt()``, which cancels an LLM call
*while the model is still generating tokens*.  The cancellation is
instant because the asyncio task driving ``arun()`` is cancelled at
the very next ``await`` boundary — typically inside the streaming
HTTP read.

Contrast with ``conversation.pause()`` where the pause cannot take
effect until the current blocking LLM call finishes.

Usage:
    LLM_API_KEY=... python examples/01_standalone_sdk/50_async_cancellation.py

    # Press Ctrl-C at any time to interrupt, or wait for the auto timer.
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
# so the LLM call is visibly in-flight when we interrupt.
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


# ── Interruption machinery ──────────────────────────────────────────
AUTO_CANCEL_SECONDS = float(os.getenv("AUTO_CANCEL_SECONDS", "5"))


def _request_interrupt(source: str) -> None:
    """Interrupt the conversation from any context."""
    print(f"\n\n{'=' * 60}")
    print(f"⚡ Interrupt requested ({source})")
    print(f"   Tokens received before interrupt: {token_count}")
    print(f"{'=' * 60}\n")
    conversation.interrupt()


async def _auto_interrupt_timer() -> None:
    """Fire after AUTO_CANCEL_SECONDS to show programmatic interruption."""
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
    print("Async LLM Interrupt Demo")
    print("=" * 60)
    print(f"Model          : {model}")
    print(f"Auto-interrupt : {AUTO_CANCEL_SECONDS}s  (or press Ctrl-C)")
    print("=" * 60)
    print()

    # ── Launch arun() alongside the interrupt timer ──────────────────
    timer_task = asyncio.create_task(_auto_interrupt_timer())
    wall_start = time.monotonic()

    # arun() catches CancelledError internally and returns cleanly
    # with status=PAUSED, so no CancelledError propagates here.
    await conversation.arun()

    timer_task.cancel()
    elapsed_ms = (time.monotonic() - wall_start) * 1000

    print()
    print("─" * 60)
    print(f"Conversation status : {conversation.state.execution_status}")
    print(f"Wall time           : {elapsed_ms:.0f} ms")
    print(f"Tokens streamed     : {token_count}")
    print()
    print("With pause(), the LLM HTTP call must finish before the")
    print("pause takes effect.  With interrupt(), the asyncio task")
    print("is cancelled at the next await boundary — instantly.")
    print("─" * 60)

    cost = llm.metrics.accumulated_cost
    print(f"\nEXAMPLE_COST: {cost}")


asyncio.run(main())
