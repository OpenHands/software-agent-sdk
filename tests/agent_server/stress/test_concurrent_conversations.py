"""Stress test: many separate conversations running concurrently.

Bug class this catches:
    - Lease contention between conversations sharing persistence layer.
    - Persistence write contention (one conversation's append blocking another).
    - Cross-conversation event leaks (events ending up in the wrong log).
    - Connection-pool / thread-pool exhaustion that silently serializes runs.

Distinct from test_parallel_subagents.py:
    parallel_subagents tests N sub-agents in *one* conversation. This tests N
    *separate* conversations, so the hot path is conversation_lease,
    persistence/store, and pub_sub broadcasting — not TaskManager.
"""

import asyncio
import time
from uuid import UUID

import pytest

from openhands.agent_server.conversation_service import ConversationService
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm import Message, TextContent
from tests.agent_server.stress.budgets import CONCURRENT_CONVERSATIONS
from tests.agent_server.stress.probe import ResourceProbe
from tests.agent_server.stress.scripts import (
    SlowTestLLM,
    start_conversation_with_test_llm,
    wait_for_terminal,
)


pytestmark = pytest.mark.stress


def _build_simple_llm(latency_s: float) -> SlowTestLLM:
    """LLM scripted with one text response (no tool calls).

    The agent terminates after the first response when it sees no tool
    calls, so one scripted message per conversation is enough — additional
    scripted messages would never be consumed.
    """
    llm = SlowTestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="done")])],
        latency_s=latency_s,
    )
    # from_messages is typed as returning the parent TestLLM; narrow.
    assert isinstance(llm, SlowTestLLM)
    return llm


async def _start_one(
    conversation_service: ConversationService,
    *,
    workspace: str,
    latency_s: float,
    usage_id: str,
) -> tuple[UUID, SlowTestLLM]:
    parent_llm = _build_simple_llm(latency_s)
    info = await start_conversation_with_test_llm(
        conversation_service,
        parent_llm=parent_llm,
        workspace_dir=workspace,
        usage_id=usage_id,
        initial_text="hello",
    )
    return info.id, parent_llm


async def _run_and_wait(
    client, conversation_id: UUID
) -> tuple[float, ConversationExecutionStatus]:
    t0 = time.monotonic()
    run_resp = await client.post(f"/api/conversations/{conversation_id.hex}/run")
    assert run_resp.status_code == 200, run_resp.text
    status = await wait_for_terminal(client, conversation_id, timeout_s=60.0)
    return time.monotonic() - t0, status


async def test_concurrent_conversations_isolated_and_fast(
    conversation_service: ConversationService,
    client,
    tmp_path,
    probe: ResourceProbe,
):
    """N concurrent conversations: all complete, no cross-leaks, parallelism."""
    n = CONCURRENT_CONVERSATIONS.n_conversations
    latency_s = CONCURRENT_CONVERSATIONS.per_call_latency_s
    workspace = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir()

    # 1. Single-conversation reference timing — same loop, same fixture.
    ref_id, ref_llm = await _start_one(
        conversation_service,
        workspace=workspace,
        latency_s=latency_s,
        usage_id="conc-ref",
    )
    ref_wall, ref_status = await _run_and_wait(client, ref_id)
    assert ref_status == ConversationExecutionStatus.FINISHED
    assert ref_llm.remaining_responses == 0

    # Snapshot probe state between reference and concurrent runs so the
    # RSS budget below measures the concurrent run only — see
    # test_parallel_subagents.py for the same pattern.
    pre_concurrent_idx = len(probe.samples)
    assert pre_concurrent_idx > 0, "ResourceProbe yielded no samples?"
    pre_concurrent_rss_mb = probe.samples[-1].rss_mb

    # 2. Now N concurrent conversations.
    started = await asyncio.gather(
        *[
            _start_one(
                conversation_service,
                workspace=workspace,
                latency_s=latency_s,
                usage_id=f"conc-{i}",
            )
            for i in range(n)
        ]
    )

    t0 = time.monotonic()
    results = await asyncio.gather(
        *[_run_and_wait(client, conv_id) for conv_id, _llm in started]
    )
    concurrent_wall = time.monotonic() - t0

    # 3. Every conversation finished cleanly.
    for i, (_wall, status) in enumerate(results):
        assert status == ConversationExecutionStatus.FINISHED, (
            f"conversation {i} ended in {status}, expected FINISHED. "
            f"Possible lease contention or persistence error."
        )

    # 4. Each LLM was actually drained — catches "all conversations sharing
    #    one LLM" or "wrong LLM picked up" regressions.
    for i, (_, llm) in enumerate(started):
        assert llm.remaining_responses == 0, (
            f"conversation {i} LLM not drained "
            f"({llm.remaining_responses} responses left). Cross-conversation "
            f"event leak or LLM mix-up?"
        )

    # 5. Parallelism. Concurrent wall must be far less than n × ref_wall.
    serial_estimate = ref_wall * n
    budget = ref_wall * CONCURRENT_CONVERSATIONS.wall_time_factor
    assert concurrent_wall < budget, (
        f"concurrent wall ({concurrent_wall:.2f}s) > budget ({budget:.2f}s "
        f"= ref {ref_wall:.2f}s × {CONCURRENT_CONVERSATIONS.wall_time_factor}). "
        f"Serial estimate would be {serial_estimate:.2f}s. Conversations "
        f"are running effectively in series — likely a global lock somewhere."
    )

    # 6. Persistence sanity: the set of dirs on disk must match exactly the
    #    set of conversation IDs we started. Asserting on the ID set (not
    #    just the count) catches "right count, wrong IDs" — e.g. a
    #    conversation failed to start but left a directory behind and a
    #    retry succeeded with a different ID.
    expected_ids = {ref_id, *(conv_id for conv_id, _llm in started)}
    on_disk_ids = {UUID(d.name) for d in (tmp_path / "persist").iterdir() if d.is_dir()}
    assert on_disk_ids == expected_ids, (
        f"persisted dirs don't match started conversations. "
        f"missing={expected_ids - on_disk_ids}, "
        f"extra={on_disk_ids - expected_ids}."
    )

    # 7. Resource budget. Compared against the snapshot taken between
    #    the reference and concurrent runs, so the spike from the
    #    reference run isn't attributed here.
    concurrent_peak_rss_mb = max(
        (s.rss_mb for s in probe.samples[pre_concurrent_idx:]),
        default=pre_concurrent_rss_mb,
    )
    rss_growth = (concurrent_peak_rss_mb - pre_concurrent_rss_mb) / max(
        pre_concurrent_rss_mb, 1.0
    )
    assert rss_growth < CONCURRENT_CONVERSATIONS.rss_growth_factor, (
        f"RSS grew {rss_growth:.2f}× during concurrent run (budget < "
        f"{CONCURRENT_CONVERSATIONS.rss_growth_factor}×). Conversation "
        f"teardown may not be releasing memory."
    )
