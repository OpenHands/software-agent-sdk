"""Stop and steer a running conversation from outside the run, the way a
program does: `pause()` from another thread, `arun()` with `interrupt()`, the
cancellation token, and `AsyncCallbackWrapper` for an asyncio program. The
model is a scripted `TestLLM` (slowed down where the timing matters); the
tools are the real terminal and the `sleeper` tool from `S01/sleeper.py`.

Usage: control.py MODE
  pause           pause() between steps, while idle, twice, then resume
  pause-mid-call  pause() while an LLM call is in flight
  async-callback  a coroutine on the main loop receives events from run()
  interrupt       interrupt() cancels arun() mid-LLM-call; resume; fallbacks
  cancel-token    interrupt() during a batch of tool calls skips the rest
"""

import asyncio
import sys
import threading
import time

from S01.sleeper import TIMELINE  # registers "SleeperTool"
from sdk_verify import emit, run_paths, script_messages

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.event import (
    ActionEvent,
    InterruptEvent,
    ObservationEvent,
    PauseEvent,
)
from openhands.sdk.testing import TestLLM
from openhands.tools.terminal import TerminalTool


mode = sys.argv[1]
WORKSPACE = str(run_paths()["workspace"])
out: dict = {"ok": True, "mode": mode}


def count(conversation, kind) -> int:
    return sum(isinstance(e, kind) for e in conversation.state.events)


def sleeper(label: str, seconds: float = 0.6) -> dict:
    return {"tool": "sleeper", "args": {"label": label, "seconds": seconds}}


class SlowLLM(TestLLM):
    """A scripted model that takes `delay` seconds per call (sync and async)."""

    delay: float = 0.0
    in_flight: threading.Event | None = None

    def completion(self, *args, **kwargs):
        if self.in_flight is not None:
            self.in_flight.set()
        time.sleep(self.delay)
        return super().completion(*args, **kwargs)

    async def acompletion(self, *args, **kwargs):
        if self.in_flight is not None:
            self.in_flight.set()
        await asyncio.sleep(self.delay)
        return await super().acompletion(*args, **kwargs)


def slow_llm(script, delay: float) -> SlowLLM:
    llm = SlowLLM.from_messages(script_messages(script), usage_id="agent")
    llm.delay = delay
    llm.in_flight = threading.Event()
    return llm


def conversation_for(llm, tools=("SleeperTool",), **kw):
    return Conversation(
        agent=Agent(llm=llm, tools=[Tool(name=t) for t in tools], **kw),
        workspace=WORKSPACE,
        visualizer=None,
    )


if mode == "pause":
    script = [
        sleeper("one"),
        sleeper("two"),
        sleeper("three"),
        sleeper("four"),
        {"finish": "all four"},
    ]
    llm = TestLLM.from_messages(script_messages(script), usage_id="agent")
    conversation = conversation_for(llm)

    # IDLE -> PAUSED, and a second pause() adds nothing.
    conversation.pause()
    out["idle_pause_status"] = conversation.state.execution_status.value
    conversation.pause()
    out["idle_pause_events"] = count(conversation, PauseEvent)

    conversation.send_message("Sleep four times, then finish.")
    thread = threading.Thread(target=conversation.run)
    thread.start()
    while count(conversation, ObservationEvent) < 1 and thread.is_alive():
        time.sleep(0.02)
    conversation.pause()  # from this (other) thread while RUNNING
    conversation.pause()
    thread.join(timeout=60)
    out |= {
        "status_after_pause": conversation.state.execution_status.value,
        "pause_events": count(conversation, PauseEvent),
        "steps_before_stop": count(conversation, ObservationEvent),
        "llm_calls_before_stop": llm.call_count,
    }
    conversation.run()
    out |= {
        "status_after_resume": conversation.state.execution_status.value,
        "steps_after_resume": count(conversation, ObservationEvent),
        "script_left": llm.remaining_responses,
    }
    conversation.close()

elif mode == "pause-mid-call":
    llm = slow_llm([sleeper("only", 0.1), {"finish": "done"}], delay=1.5)
    conversation = conversation_for(llm)
    conversation.send_message("Sleep once, then finish.")
    thread = threading.Thread(target=conversation.run)
    thread.start()
    llm.in_flight.wait(timeout=30)
    time.sleep(0.2)
    paused_at = time.monotonic()
    conversation.pause()
    pause_returned_after = round(time.monotonic() - paused_at, 2)
    thread.join(timeout=60)
    out |= {
        "pause_returned_after_s": pause_returned_after,
        "status": conversation.state.execution_status.value,
        "llm_calls": llm.call_count,
        # The call in flight finished and its action still ran: the pause
        # took effect at the next step boundary.
        "actions": count(conversation, ActionEvent),
        "observations": count(conversation, ObservationEvent),
        "pause_events": count(conversation, PauseEvent),
    }
    conversation.run()
    out["status_after_resume"] = conversation.state.execution_status.value
    conversation.close()

elif mode == "async-callback":
    from openhands.sdk.utils.async_utils import AsyncCallbackWrapper

    received: list[str] = []
    threads: set[str] = set()

    async def on_event(event) -> None:
        threads.add(threading.current_thread().name)
        await asyncio.sleep(0)
        received.append(type(event).__name__)

    async def main() -> dict:
        loop = asyncio.get_running_loop()
        wrapper = AsyncCallbackWrapper(on_event, loop=loop)
        llm = TestLLM.from_messages(
            script_messages(
                [
                    {"tool": "terminal", "args": {"command": "echo async-callback"}},
                    {"finish": "callback done"},
                ]
            ),
            usage_id="agent",
        )
        conversation = Conversation(
            agent=Agent(llm=llm, tools=[Tool(name=TerminalTool.name)]),
            workspace=WORKSPACE,
            callbacks=[wrapper],
            visualizer=None,
        )
        conversation.send_message("Echo, then finish.")
        run_thread: list[str] = []

        def run() -> None:
            run_thread.append(threading.current_thread().name)
            conversation.run()

        await loop.run_in_executor(None, run)
        await asyncio.sleep(0.1)
        wrapper.wait_for_pending(timeout=5)
        result = {
            "events_in_state": len(conversation.state.events),
            "run_thread_is_main": run_thread == [threading.main_thread().name],
            "status": conversation.state.execution_status.value,
        }
        conversation.close()
        return result

    out |= asyncio.run(main())
    out |= {
        "received": len(received),
        "kinds": sorted(set(received)),
        "callback_threads": sorted(threads),
        "callbacks_on_main_thread": threads == {threading.main_thread().name},
    }

elif mode == "interrupt":
    llm = slow_llm(
        [
            {"tool": "terminal", "args": {"command": "echo resumed"}},
            {"finish": "after interrupt"},
        ],
        delay=30,
    )
    conversation = conversation_for(llm, tools=(TerminalTool.name,))
    conversation.send_message("Echo, then finish.")
    caught: list[str] = []

    async def drive() -> None:
        try:
            await conversation.arun()
        except BaseException as exc:  # report, do not judge
            caught.append(type(exc).__name__)

    thread = threading.Thread(target=lambda: asyncio.run(drive()))
    thread.start()
    llm.in_flight.wait(timeout=30)
    started = time.monotonic()
    threading.Thread(target=conversation.interrupt).start()  # from another thread
    thread.join(timeout=60)
    out |= {
        "stopped_after_s": round(time.monotonic() - started, 2),
        "raised": caught,
        "status": conversation.state.execution_status.value,
        "interrupt_events": count(conversation, InterruptEvent),
        "actions_before_resume": count(conversation, ActionEvent),
        "script_left_after_interrupt": llm.remaining_responses,
    }
    llm.delay = 0.0
    conversation.run()
    out |= {
        "status_after_resume": conversation.state.execution_status.value,
        "script_left_after_resume": llm.remaining_responses,
    }
    conversation.close()

    # With no arun() in flight interrupt() is a pause(): while idle, and
    # during a synchronous run().
    idle = conversation_for(TestLLM.from_messages([], usage_id="agent"))
    idle.interrupt()
    out["idle_interrupt"] = {
        "status": idle.state.execution_status.value,
        "pause_events": count(idle, PauseEvent),
        "interrupt_events": count(idle, InterruptEvent),
    }
    idle.close()
    sync_llm = slow_llm([sleeper("a", 0.1), {"finish": "x"}], delay=1.0)
    sync = conversation_for(sync_llm)
    sync.send_message("Sleep, then finish.")
    thread = threading.Thread(target=sync.run)
    thread.start()
    sync_llm.in_flight.wait(timeout=30)
    sync.interrupt()
    thread.join(timeout=60)
    out["sync_interrupt"] = {
        "status": sync.state.execution_status.value,
        "pause_events": count(sync, PauseEvent),
        "interrupt_events": count(sync, InterruptEvent),
        "llm_calls": sync_llm.call_count,
    }
    sync.close()

elif mode == "cancel-token":
    TIMELINE.clear()
    batch = {"tools": [sleeper(f"call-{n}", 1.0) for n in range(4)]}
    llm = TestLLM.from_messages(
        script_messages([batch, {"finish": "batch done"}]), usage_id="agent"
    )
    conversation = conversation_for(llm, tool_concurrency_limit=1)
    conversation.send_message("Run the batch.")
    tokens: list = []

    async def drive() -> None:
        await conversation.arun()

    def watch_token() -> None:
        while thread.is_alive():
            token = conversation.cancel_token
            if token is not None and (not tokens or tokens[-1] is not token):
                tokens.append(token)
            time.sleep(0.02)

    thread = threading.Thread(target=lambda: asyncio.run(drive()))
    thread.start()
    watcher = threading.Thread(target=watch_token)
    watcher.start()
    while count(conversation, ActionEvent) < 4 and thread.is_alive():
        time.sleep(0.02)
    time.sleep(0.3)  # call-0 is running
    conversation.interrupt()
    thread.join(timeout=60)
    watcher.join()
    time.sleep(1.2)  # let a worker that was already running finish
    first_token = tokens[0] if tokens else None
    out |= {
        "status": conversation.state.execution_status.value,
        "token_cancelled": bool(first_token and first_token.is_cancelled),
        "calls_started": sorted(t["label"] for t in TIMELINE),
        "actions": count(conversation, ActionEvent),
        "interrupt_events": count(conversation, InterruptEvent),
    }
    # The next run gets a fresh, uncancelled token and finishes the work.
    thread = threading.Thread(target=lambda: asyncio.run(drive()))
    watcher = threading.Thread(target=watch_token)
    thread.start()
    watcher.start()
    thread.join(timeout=60)
    watcher.join()
    out |= {
        "tokens_seen": len(tokens),
        "second_token_is_new": len(tokens) >= 2 and tokens[-1] is not tokens[0],
        "second_token_cancelled": bool(len(tokens) >= 2 and tokens[-1].is_cancelled),
        "status_after_resume": conversation.state.execution_status.value,
    }
    conversation.close()

else:
    raise SystemExit(f"unknown mode {mode}")

emit(out)
