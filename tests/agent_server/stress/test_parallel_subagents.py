"""Stress test: many parallel sub-agents in a single conversation.

Bug class this catches:
    - Event-attribution races (tasks getting mixed sub-agent results).
    - Pub-sub corruption when N sub-agents publish concurrently.
    - Sub-agent registry leaks (factories never released).
    - Tool concurrency regressions that silently serialize parallel tool calls.

Why a SlowTestLLM is required:
    Stock TestLLM responds in microseconds. Eight sub-agents in serial finish
    so fast that wall time tells us nothing about whether they actually ran in
    parallel. Adding ~200 ms per LLM call makes the gap between serial
    (~8 × 200 ms) and parallel (~200 ms) large enough to assert against.

Subtle gotcha (manager.py:314):
    The TaskManager model_copies the sub-agent's LLM before running it.
    ``_call_count`` (an int) is independent on the copy; ``_scripted_responses``
    (a deque) is reference-shared. So we assert via ``remaining_responses``,
    not ``call_count``, on the original sub-agent LLM.
"""

import json
import time

import pytest

from openhands.agent_server.conversation_service import ConversationService
from openhands.sdk import Agent, Tool
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.subagent.registry import _reset_registry_for_tests, register_agent
from openhands.tools.task import TaskToolSet
from tests.agent_server.stress.budgets import PARALLEL_SUBAGENTS
from tests.agent_server.stress.probe import ResourceProbe
from tests.agent_server.stress.scripts import (
    SlowTestLLM,
    start_conversation_with_test_llm,
    text_message,
    wait_for_terminal,
)


pytestmark = pytest.mark.stress


@pytest.fixture(autouse=True)
def _reset_registry():
    """Sub-agent registry is module-global; isolate per test."""
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _task_tool_call(call_id: str, subagent_type: str, prompt: str) -> MessageToolCall:
    return MessageToolCall(
        id=call_id,
        name="task",
        arguments=json.dumps({"prompt": prompt, "subagent_type": subagent_type}),
        origin="completion",
    )


def _register_subagents(n: int, latency_s: float) -> list[SlowTestLLM]:
    sub_llms: list[SlowTestLLM] = []
    for i in range(n):
        sub_llm = SlowTestLLM.from_messages(
            [text_message(f"sub-agent {i} done")],
            latency_s=latency_s,
        )
        # from_messages is typed as returning the parent TestLLM; narrow.
        assert isinstance(sub_llm, SlowTestLLM)
        sub_llms.append(sub_llm)
        register_agent(
            name=f"stress_subagent_{i}",
            # `_bound=sub_llm` captures the current sub_llm at definition
            # time; without it, all factories close over the loop variable
            # and end up returning the last `sub_llm` only.
            factory_func=lambda llm, _bound=sub_llm: Agent(llm=_bound, tools=[]),
            description=f"stress test sub-agent {i}",
        )
    return sub_llms


def _build_parent_llm(n: int, latency_s: float) -> SlowTestLLM:
    """Parent emits one Message containing n parallel task tool calls, then a
    terminal text message after observations come back."""
    delegations = Message(
        role="assistant",
        content=[TextContent(text="delegating")],
        tool_calls=[
            _task_tool_call(
                call_id=f"call_{i}",
                subagent_type=f"stress_subagent_{i}",
                prompt=f"task {i}",
            )
            for i in range(n)
        ],
    )
    llm = SlowTestLLM.from_messages(
        [delegations, text_message("all done")], latency_s=latency_s
    )
    # from_messages is typed as returning the parent TestLLM; narrow.
    assert isinstance(llm, SlowTestLLM)
    return llm


async def _run_once(
    conversation_service: ConversationService,
    client,
    workspace: str,
    *,
    n_subagents: int,
    tool_concurrency_limit: int,
    latency_s: float,
    usage_id: str,
) -> tuple[float, list[SlowTestLLM], ConversationExecutionStatus]:
    sub_llms = _register_subagents(n_subagents, latency_s)
    parent_llm = _build_parent_llm(n_subagents, latency_s)
    info = await start_conversation_with_test_llm(
        conversation_service,
        parent_llm=parent_llm,
        workspace_dir=workspace,
        usage_id=usage_id,
        tools=[Tool(name=TaskToolSet.name)],
        tool_concurrency_limit=tool_concurrency_limit,
        initial_text=f"run {n_subagents} task(s)",
    )

    t0 = time.monotonic()
    run_resp = await client.post(f"/api/conversations/{info.id.hex}/run")
    assert run_resp.status_code == 200, run_resp.text
    status = await wait_for_terminal(client, info.id, timeout_s=30.0)
    return time.monotonic() - t0, sub_llms, status


async def test_parallel_subagents_all_complete(
    conversation_service: ConversationService,
    client,
    tmp_path,
    probe: ResourceProbe,
):
    """N=8 sub-agents in parallel: all complete, parallelism observed, no leak."""
    n = PARALLEL_SUBAGENTS.n_subagents
    latency_s = PARALLEL_SUBAGENTS.per_call_latency_s
    workspace = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir()

    # Single-agent reference, then registry reset.
    single_wall, single_subs, single_status = await _run_once(
        conversation_service,
        client,
        workspace,
        n_subagents=1,
        tool_concurrency_limit=1,
        latency_s=latency_s,
        usage_id="stress-parent-single",
    )
    assert single_status == ConversationExecutionStatus.FINISHED
    assert single_subs[0].remaining_responses == 0
    _reset_registry_for_tests()

    # Snapshot probe state between the reference run and the parallel run
    # so the resource assertions below measure *only* the parallel run.
    # Without this the peak/baseline include any RSS spike caused by the
    # single-agent run, which is unrelated to the leak we're checking.
    pre_parallel_idx = len(probe.samples)
    pre_parallel_rss_mb = probe.samples[-1].rss_mb

    # Now the actual n-sub-agent run.
    parallel_wall, sub_llms, status = await _run_once(
        conversation_service,
        client,
        workspace,
        n_subagents=n,
        tool_concurrency_limit=n,
        latency_s=latency_s,
        usage_id="stress-parent-parallel",
    )

    # 1. Each sub-agent ran exactly once. We assert on remaining_responses
    #    (drained queue) rather than call_count: TaskManager model_copies the
    #    sub-agent LLM (manager.py:314), and the copy gets its own integer
    #    _call_count, while the deque of scripted responses is reference-
    #    shared. remaining_responses reflects whether the original LLM's
    #    queue was actually drained; call_count on the original always
    #    reads 0.
    assert status == ConversationExecutionStatus.FINISHED
    for i, sub in enumerate(sub_llms):
        assert sub.remaining_responses == 0, (
            f"sub-agent {i} still has {sub.remaining_responses} unconsumed "
            f"responses (expected 0). Likely cause: another sub-agent ran "
            f"twice while this one was skipped."
        )

    # 2. Parallelism actually happened. Without this, a regression that
    #    serializes tool execution silently passes.
    budget = single_wall * PARALLEL_SUBAGENTS.wall_time_factor
    assert parallel_wall < budget, (
        f"parallel wall ({parallel_wall:.2f}s) exceeded budget "
        f"({budget:.2f}s = {single_wall:.2f}s × "
        f"{PARALLEL_SUBAGENTS.wall_time_factor}). Sub-agents likely serialized."
    )

    # 3. Resource budget. Compared against the snapshot taken between the
    #    single-agent reference run and the parallel run, so the spike
    #    from the reference run isn't attributed here.
    parallel_peak_rss_mb = max(
        (s.rss_mb for s in probe.samples[pre_parallel_idx:]),
        default=pre_parallel_rss_mb,
    )
    rss_growth = (parallel_peak_rss_mb - pre_parallel_rss_mb) / max(
        pre_parallel_rss_mb, 1.0
    )
    assert rss_growth < PARALLEL_SUBAGENTS.rss_growth_factor, (
        f"RSS grew {rss_growth:.2f}× during the parallel run "
        f"({pre_parallel_rss_mb:.1f} MB → peak {parallel_peak_rss_mb:.1f} MB). "
        f"Budget: < {PARALLEL_SUBAGENTS.rss_growth_factor}×."
    )
    assert probe.fd_delta() < PARALLEL_SUBAGENTS.max_fd_growth, (
        f"FDs grew by {probe.fd_delta()} (budget < "
        f"{PARALLEL_SUBAGENTS.max_fd_growth}). Possible FD leak in sub-agent "
        f"teardown."
    )
