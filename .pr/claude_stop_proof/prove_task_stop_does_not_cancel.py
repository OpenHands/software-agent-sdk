"""Proof script: Claude DelegationManager stop_task() does not cancel execution.

This script runs a Claude DelegationManager background task, requests stop mid-run,
then logs evidence that the underlying conversation continues executing tool-call
steps until it completes naturally.

It is intentionally stored under `.pr/` (temporary PR artifacts).

Usage:
  uv run python .pr/claude_stop_proof/prove_task_stop_does_not_cancel.py \
    --model openai/gpt-5-nano \
    --ticks 50 \
    --stop-after-actions 8

Env:
  OPENAI_API_KEY must be set (no base URL required).

What this demonstrates:
  - We stop after N tool-call ActionEvents have occurred.
  - After stop_task(), the background thread remains alive.
  - Additional tool-call ActionEvents keep arriving after stop.
  - The thread only exits when conversation.run() returns naturally.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from openhands.sdk import Agent, LocalConversation
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.llm import LLM
from openhands.sdk.tool import (
    Action,
    Observation,
    Tool,
    ToolAnnotations,
    ToolDefinition,
    register_tool,
)
from openhands.sdk.tool.tool import ToolExecutor
from openhands.tools.claude.impl import DelegationManager
from openhands.tools.delegate.registration import register_agent


logger = logging.getLogger("prove_task_stop")


def _log(event: str, **fields: object) -> None:
    payload = "" if not fields else " " + json.dumps(fields, default=str)
    logger.info(f"{event}{payload}")


# Global stop telemetry shared with the background thread.
_STOP_REQUESTED = threading.Event()
_POST_STOP_TICK_EXEC_LOCK = threading.Lock()
_POST_STOP_TICK_EXEC_COUNT = 0


# ---------------------------------------------------------------------------
# A tiny, safe tool that makes it easy to generate many LLM tool-call steps.
# ---------------------------------------------------------------------------


class TickAction(Action):
    i: int


class TickObservation(Observation):
    i: int


class TickExecutor(ToolExecutor):
    def __init__(self, sleep_s: float) -> None:
        self._sleep_s = sleep_s

    def __call__(
        self, action: TickAction, conversation: LocalConversation | None = None
    ) -> TickObservation:
        _ = conversation

        post_stop_exec_count: int | None = None
        if _STOP_REQUESTED.is_set():
            global _POST_STOP_TICK_EXEC_COUNT
            with _POST_STOP_TICK_EXEC_LOCK:
                _POST_STOP_TICK_EXEC_COUNT += 1
                post_stop_exec_count = _POST_STOP_TICK_EXEC_COUNT

        _log(
            "TICK_EXEC",
            i=action.i,
            sleep_s=self._sleep_s,
            post_stop_exec_count=post_stop_exec_count,
        )
        time.sleep(self._sleep_s)
        return TickObservation.from_text(text=f"tick={action.i}", i=action.i)


class TickTool(ToolDefinition[TickAction, TickObservation]):
    """A deterministic tool for repeated tool-call steps."""

    @classmethod
    def create(
        cls,
        conv_state,  # noqa: ARG003
        sleep_s: float = 0.25,
    ) -> list[TickTool]:
        return [
            cls(
                action_type=TickAction,
                observation_type=TickObservation,
                description=(
                    "Call this tool repeatedly to produce a numbered tick. "
                    "Input: {i: int}. Output contains tick number."
                ),
                annotations=ToolAnnotations(
                    title="tick",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                executor=TickExecutor(sleep_s=sleep_s),
            )
        ]


# Register the tool once (global tool registry)
register_tool(TickTool.name, TickTool)


def _count_action_events(conv: LocalConversation, tool_name: str) -> int:
    with conv.state:
        return sum(
            1
            for e in conv.state.events
            if isinstance(e, ActionEvent) and e.tool_name == tool_name
        )


def _tail_action_events(
    conv: LocalConversation, tool_name: str, n: int = 5
) -> list[dict]:
    with conv.state:
        actions = [
            e
            for e in conv.state.events
            if isinstance(e, ActionEvent) and e.tool_name == tool_name
        ]
        tail = actions[-n:]

    out: list[dict] = []
    for a in tail:
        out.append(
            {
                "tool_name": a.tool_name,
                "tool_call_id": a.tool_call_id,
                "llm_response_id": a.llm_response_id,
                "summary": a.summary,
            }
        )
    return out


@dataclass(frozen=True)
class Evidence:
    t0: float
    stop_at_action_count: int
    min_post_stop_actions: int
    t_stop_request: float
    action_count_at_stop: int
    first_post_stop_action_count: int | None
    t_first_post_stop_action: float | None
    action_count_at_end: int
    post_stop_tick_exec_count: int
    t_thread_exit: float | None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        default="openai/gpt-5-nano",
        help="Model name passed to openhands.sdk.LLM (default: openai/gpt-5-nano)",
    )
    parser.add_argument("--ticks", type=int, default=50)
    parser.add_argument(
        "--stop-after-actions",
        type=int,
        default=8,
        help="Stop once this many tick ActionEvents have happened.",
    )
    parser.add_argument(
        "--stop-wait-seconds",
        type=float,
        default=300.0,
        help=(
            "Max time to wait until stop-after-actions is reached before stopping. "
            "(This is separate from --max-watch-seconds, which is used after stop.)"
        ),
    )
    parser.add_argument(
        "--min-post-stop-actions",
        type=int,
        default=2,
        help="Require at least this many additional tick steps after stop.",
    )

    parser.add_argument("--tick-sleep", type=float, default=0.25)
    parser.add_argument("--poll-interval", type=float, default=0.1)
    parser.add_argument("--max-watch-seconds", type=float, default=30.0)
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is required")

    # Make logs unambiguous even with multiple threads.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03dZ %(levelname)s [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    t0 = time.monotonic()

    llm = LLM(model=args.model, api_key=api_key, base_url=None)

    # Parent conversation: only used as a carrier for workspace + parent LLM.
    tmp_root = Path(".pr/claude_stop_proof/_run")
    tmp_root.mkdir(parents=True, exist_ok=True)
    workspace = tmp_root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    parent_agent = Agent(llm=llm, tools=[])
    parent = LocalConversation(
        agent=parent_agent,
        workspace=str(workspace),
        persistence_dir=str(tmp_root / "parent_state"),
        visualizer=None,
    )

    # Our subagent only has the tick tool. That keeps risk/confirmation out.
    def _tick_agent_factory(sub_llm: LLM) -> Agent:
        return Agent(
            llm=sub_llm,
            tools=[Tool(name=TickTool.name, params={"sleep_s": args.tick_sleep})],
        )

    agent_type = f"stop_proof_tick_{int(time.time())}"
    register_agent(
        name=agent_type,
        factory_func=_tick_agent_factory,
        description="Temporary agent for proving stop does not cancel run()",
    )

    mgr = DelegationManager(max_tasks=2)

    prompt_lines = [
        "You are running in a test harness.",
        (
            "Task: Call the `tick` tool exactly "
            f"{args.ticks} times, in order from i=1 to i={args.ticks}."
        ),
        "Rules:",
        "- You MUST call `tick` once per number.",
        "- Do NOT batch multiple ticks into one call.",
        "- After each tick observation, immediately call the next tick.",
        (
            f"- After finishing tick {args.ticks}, call the finish tool with a "
            "short message."
        ),
    ]
    prompt = "\n".join(prompt_lines) + "\n"

    task = mgr.start_task(
        prompt=prompt,
        subagent_type=agent_type,
        run_in_background=True,
        max_turns=args.ticks + 20,
        conversation=parent,
        description="stop-proof",
    )

    assert task.thread is not None
    assert task.conversation is not None

    tool_name = TickTool.name

    _log("TASK_STARTED", task_id=task.id, thread_alive=task.thread.is_alive())

    # Wait until we have enough tool-call steps to claim we're "mid-run".
    stop_at = args.stop_after_actions
    deadline = time.monotonic() + args.stop_wait_seconds

    last_count = -1
    while time.monotonic() < deadline and task.thread.is_alive():
        count = _count_action_events(task.conversation, tool_name=tool_name)
        if count != last_count:
            _log("ACTION_COUNT", count=count)
            last_count = count
        if count >= stop_at:
            break
        time.sleep(args.poll_interval)

    action_count_at_stop = _count_action_events(task.conversation, tool_name=tool_name)

    if action_count_at_stop < stop_at:
        _log(
            "PRE_STOP_THRESHOLD_NOT_MET",
            requested_stop_after_actions=stop_at,
            observed_actions_before_stop=action_count_at_stop,
            stop_wait_seconds=args.stop_wait_seconds,
        )

    _log(
        "REQUEST_STOP",
        task_id=task.id,
        action_count=action_count_at_stop,
        thread_alive=task.thread.is_alive(),
    )

    _STOP_REQUESTED.set()
    t_stop = time.monotonic()
    _ = mgr.stop_task(task.id)

    _log(
        "STOP_RETURNED",
        task_status=str(task.status),
        thread_alive=task.thread.is_alive(),
    )

    # After stop, watch for *new* ActionEvents / tool executions. If we observe
    # >= N additional steps after stop, that's proof run() continues.
    first_post_stop_count: int | None = None
    t_first_post_stop: float | None = None

    target_action_count = action_count_at_stop + args.min_post_stop_actions
    proved_post_stop_steps = False

    post_stop_deadline = time.monotonic() + args.max_watch_seconds
    while time.monotonic() < post_stop_deadline and task.thread.is_alive():
        count_now = _count_action_events(task.conversation, tool_name=tool_name)
        with _POST_STOP_TICK_EXEC_LOCK:
            post_stop_tick_exec_count = _POST_STOP_TICK_EXEC_COUNT

        if first_post_stop_count is None and count_now > action_count_at_stop:
            first_post_stop_count = count_now
            t_first_post_stop = time.monotonic()
            _log(
                "POST_STOP_ACTIONS_OBSERVED",
                action_count_at_stop=action_count_at_stop,
                action_count_now=count_now,
                seconds_after_stop=round(t_first_post_stop - t_stop, 3),
                tail=_tail_action_events(task.conversation, tool_name),
            )

        if (
            count_now >= target_action_count
            or post_stop_tick_exec_count >= args.min_post_stop_actions
        ):
            proved_post_stop_steps = True
            _log(
                "POST_STOP_PROOF_REACHED",
                min_post_stop_actions=args.min_post_stop_actions,
                action_count_at_stop=action_count_at_stop,
                action_count_now=count_now,
                target_action_count=target_action_count,
                post_stop_tick_exec_count=post_stop_tick_exec_count,
                seconds_after_stop=round(time.monotonic() - t_stop, 3),
                tail=_tail_action_events(task.conversation, tool_name),
            )
            break

        time.sleep(args.poll_interval)

    # Now wait for natural completion (or timeout).
    task.thread.join(timeout=args.max_watch_seconds)
    t_exit: float | None = None
    if not task.thread.is_alive():
        t_exit = time.monotonic()

    action_count_at_end = _count_action_events(task.conversation, tool_name=tool_name)
    with _POST_STOP_TICK_EXEC_LOCK:
        post_stop_tick_exec_count = _POST_STOP_TICK_EXEC_COUNT

    evidence = Evidence(
        t0=t0,
        stop_at_action_count=stop_at,
        min_post_stop_actions=args.min_post_stop_actions,
        t_stop_request=t_stop,
        action_count_at_stop=action_count_at_stop,
        first_post_stop_action_count=first_post_stop_count,
        t_first_post_stop_action=t_first_post_stop,
        action_count_at_end=action_count_at_end,
        post_stop_tick_exec_count=post_stop_tick_exec_count,
        t_thread_exit=t_exit,
    )

    _log(
        "SUMMARY",
        task_id=task.id,
        status=str(task.status),
        thread_alive=task.thread.is_alive(),
        seconds_to_stop_request=round(evidence.t_stop_request - evidence.t0, 3),
        actions_at_stop=evidence.action_count_at_stop,
        first_post_stop_action_count=evidence.first_post_stop_action_count,
        min_post_stop_actions=evidence.min_post_stop_actions,
        proved_post_stop_steps=proved_post_stop_steps,
        action_count_at_end=evidence.action_count_at_end,
        post_stop_tick_exec_count=evidence.post_stop_tick_exec_count,
        seconds_after_stop_to_new_actions=None
        if evidence.t_first_post_stop_action is None
        else round(evidence.t_first_post_stop_action - evidence.t_stop_request, 3),
        seconds_to_thread_exit=None
        if evidence.t_thread_exit is None
        else round(evidence.t_thread_exit - evidence.t0, 3),
    )

    # Persist a machine-readable artifact.
    out_path = tmp_root / f"evidence_{task.id}.json"
    out_path.write_text(json.dumps(evidence.__dict__, indent=2, default=str))
    _log("WROTE_EVIDENCE", path=str(out_path))

    mgr.close()

    # Return non-zero if we failed to prove >= N post-stop steps.
    # This doesn't prove stop works; it might mean the task finished too fast.
    if not proved_post_stop_steps:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
