"""Cross-cutting canary: /health stays responsive under each background load.

Why this exists:
    Most agent-server bugs that cause user-visible "the server hangs" symptoms
    boil down to sync I/O on the asyncio thread. Each individual suite checks
    this in its specific scenario. This canary checks it under a representative
    mix of loads in one place — cheap to add, catches the regression class we
    forgot to test specifically.

Loads exercised:
    - Long bash command (sleep + final marker) — exercises bash_service.
    - Busy conversation listing on a seeded store — exercises persistence.
    - Active conversation runs with controlled fsync latency — exercises durability.

Loads NOT exercised here (covered by their own suites):
    - Slow webhook (test_slow_webhook.py).
    - Slow-loris websocket (test_slow_websocket_consumer.py).
    - High-volume bash output (test_high_volume_bash_output.py).
"""

import asyncio
import os
import statistics
import time
from uuid import UUID

import pytest

from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import Agent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.workspace import LocalWorkspace
from tests.agent_server.stress.budgets import EVENT_LOOP_RESPONSIVENESS
from tests.agent_server.stress.scripts import (
    SlowTestLLM,
    placeholder_llm,
    start_conversation_with_test_llm,
    text_message,
)


_TERMINAL = frozenset(
    {
        ConversationExecutionStatus.FINISHED,
        ConversationExecutionStatus.ERROR,
        ConversationExecutionStatus.STUCK,
    }
)


pytestmark = pytest.mark.stress


async def _measure_health_p95_p99(client, *, samples: int) -> tuple[float, float]:
    latencies: list[float] = []
    for _ in range(samples):
        t0 = time.monotonic()
        resp = await client.get("/health")
        latencies.append(time.monotonic() - t0)
        assert resp.status_code == 200
    quantiles = statistics.quantiles(latencies, n=100)
    # quantiles returns 99 cut-points; index 94 ≈ p95, 98 ≈ p99.
    return quantiles[94], quantiles[98]


def _assert_within_budget(name: str, p95: float, p99: float) -> None:
    assert p95 < EVENT_LOOP_RESPONSIVENESS.health_p95_s, (
        f"under load '{name}', /health p95 = {p95 * 1000:.1f} ms exceeded "
        f"{EVENT_LOOP_RESPONSIVENESS.health_p95_s * 1000:.0f} ms. The event "
        f"loop is being blocked by this load."
    )
    assert p99 < EVENT_LOOP_RESPONSIVENESS.health_p99_s, (
        f"under load '{name}', /health p99 = {p99 * 1000:.1f} ms exceeded "
        f"{EVENT_LOOP_RESPONSIVENESS.health_p99_s * 1000:.0f} ms."
    )


async def test_health_responsive_under_long_bash(
    client,
    bash_service: BashEventService,
):
    """A long bash command must not starve the event loop."""
    samples = EVENT_LOOP_RESPONSIVENESS.health_samples

    # Baseline: no load.
    p95_baseline, p99_baseline = await _measure_health_p95_p99(client, samples=samples)
    _assert_within_budget("baseline", p95_baseline, p99_baseline)

    bash_duration_s = 4
    resp = await client.post(
        "/api/bash/start_bash_command",
        json={"command": f"sleep {bash_duration_s}; echo done", "timeout": 10},
    )
    assert resp.status_code == 200, resp.text
    cmd_id = UUID(resp.json()["id"])

    # Interleave /health sampling with bash-completion polling so:
    #   (a) samples land throughout the bash lifetime (in-process ASGI makes a
    #       single /health call sub-millisecond, so a tight burst would only
    #       cover the first frame and miss the rest of the run);
    #   (b) we verify the bash command actually ran to clean exit, otherwise
    #       a silent crash/early-exit would pass the budget for the wrong
    #       reason ("/health is fast under no load").
    latencies: list[float] = []
    deadline = time.monotonic() + bash_duration_s + 10
    final = None
    while time.monotonic() < deadline:
        for _ in range(5):
            t0 = time.monotonic()
            h_resp = await client.get("/health")
            latencies.append(time.monotonic() - t0)
            assert h_resp.status_code == 200

        # `limit=1, sort_order=TIMESTAMP_DESC` so we read just the latest
        # event regardless of how many the bash command emits — the default
        # page caps at 100 and we don't want a multi-page-output regression
        # to silently miss the final BashOutput here.
        events_resp = await client.get(
            "/api/bash/bash_events/search",
            params={
                "command_id__eq": str(cmd_id),
                "limit": 1,
                "sort_order": "TIMESTAMP_DESC",
            },
        )
        assert events_resp.status_code == 200, events_resp.text
        final = next(
            (
                e
                for e in events_resp.json()["items"]
                if e["kind"] == "BashOutput" and e.get("exit_code") is not None
            ),
            None,
        )
        if final is not None:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail(f"bash command {cmd_id} never produced a final event")

    assert final["exit_code"] == 0, (
        f"background bash exited with {final['exit_code']}, expected 0; the "
        f"health-budget assertion below would have measured under no real load."
    )

    quantiles = statistics.quantiles(latencies, n=100)
    _assert_within_budget("long_bash", quantiles[94], quantiles[98])


async def test_health_responsive_under_busy_listing(
    conversation_service: ConversationService,
    client,
    tmp_path,
):
    """High-volume conversation listing in parallel must not starve /health."""
    samples = EVENT_LOOP_RESPONSIVENESS.health_samples
    workspace = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir()

    # Seed a modest store.
    seed_n = 100
    seed_sem = asyncio.Semaphore(8)

    async def _seed(i: int):
        async with seed_sem:
            request = StartConversationRequest(
                agent=Agent(llm=placeholder_llm(f"resp-canary-{i}"), tools=[]),
                workspace=LocalWorkspace(working_dir=workspace),
                autotitle=False,
            )
            await conversation_service.start_conversation(request)

    await asyncio.gather(*[_seed(i) for i in range(seed_n)])

    # Drive listing in the background.
    stop = asyncio.Event()

    async def _listing_loop():
        while not stop.is_set():
            resp = await client.get(
                "/api/conversations/search",
                params={"limit": 50, "sort_order": "CREATED_AT_DESC"},
            )
            # Without this guard, a 500 from listing would silently turn
            # the test into "/health under no load" — passing for the
            # wrong reason.
            assert resp.status_code == 200, resp.text

    bg_task = asyncio.create_task(_listing_loop())
    try:
        # Brief warm-up so the listing loop is hot before we measure.
        await asyncio.sleep(0.1)
        p95, p99 = await _measure_health_p95_p99(client, samples=samples)
        _assert_within_budget("busy_listing", p95, p99)
    finally:
        stop.set()
        await bg_task


async def test_health_responsive_under_active_conversation_runs(
    conversation_service: ConversationService,
    client,
    tmp_path,
    monkeypatch,
):
    """/health must stay responsive while running conversations persist events.

    Regression coverage for #5402: durable persistence (fsync per event
    append, length-marker advance, and base-state save) must not execute on
    the event-loop thread. If it does, N concurrent runs serialize on it and
    /health p95 blows the budget. Scheduled-arrival latency includes time
    spent waiting for a blocked event loop, which an in-process request timer
    alone would miss. Controlled fsync latency makes the regression observable
    even on a fast local filesystem; the health budgets are unchanged.
    """
    workspace = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    n = 8

    started = await asyncio.gather(
        *[
            start_conversation_with_test_llm(
                conversation_service,
                parent_llm=SlowTestLLM.from_messages(
                    [text_message("done")], latency_s=0.3
                ),
                workspace_dir=workspace,
                usage_id=f"health-canary-{i}",
                initial_text="hello",
            )
            for i in range(n)
        ]
    )
    conv_ids = [info.id for info in started]
    original_fsync = os.fsync
    fsync_calls = 0

    def slow_fsync(fd):
        nonlocal fsync_calls
        fsync_calls += 1
        time.sleep(0.02)
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", slow_fsync)
    latencies: list[float] = []
    stop = asyncio.Event()
    ready = asyncio.Event()

    async def sample_health():
        scheduled = time.monotonic() + 0.01
        ready.set()
        while not stop.is_set():
            await asyncio.sleep(max(0, scheduled - time.monotonic()))
            h_resp = await client.get("/health")
            latencies.append(time.monotonic() - scheduled)
            assert h_resp.status_code == 200
            scheduled += 0.01

    probe = asyncio.create_task(sample_health())
    try:
        # Arm the independent probe before admitting any run. It must observe
        # stalls during request admission as well as during event appends.
        await ready.wait()
        for conv_id in conv_ids:
            resp = await client.post(f"/api/conversations/{conv_id.hex}/run")
            assert resp.status_code == 200, resp.text
        pending = set(conv_ids)
        deadline = time.monotonic() + 60.0
        while pending and time.monotonic() < deadline:
            for conv_id in list(pending):
                resp = await client.get(f"/api/conversations/{conv_id.hex}")
                assert resp.status_code == 200, resp.text
                status = ConversationExecutionStatus(resp.json()["execution_status"])
                if status in _TERMINAL:
                    assert status == ConversationExecutionStatus.FINISHED, (
                        f"conversation {conv_id} ended in {status}; "
                        "the health samples may not cover a real run."
                    )
                    pending.discard(conv_id)
            await asyncio.sleep(0.01)
        assert not pending, f"conversations did not reach terminal: {pending}"
    finally:
        stop.set()
        await probe
    assert len(latencies) >= EVENT_LOOP_RESPONSIVENESS.health_samples
    assert fsync_calls > 0, "no persistence fsync was exercised"

    # Every conversation produced persisted events — the load was real.
    persist_root = tmp_path / "persist"
    for conv_id in conv_ids:
        event_files = list((persist_root / conv_id.hex / "events").glob("event-*.json"))
        assert len(event_files) >= 2, (
            f"conversation {conv_id} persisted {len(event_files)} event files; "
            "the health budget was measured without persistence load."
        )

    quantiles = statistics.quantiles(latencies, n=100)
    _assert_within_budget("active_conversation_runs", quantiles[94], quantiles[98])
