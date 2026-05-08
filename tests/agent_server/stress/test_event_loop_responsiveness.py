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

Loads NOT exercised here (covered by their own suites):
    - Slow webhook (test_slow_webhook.py).
    - Slow-loris websocket (test_slow_websocket_consumer.py).
    - High-volume bash output (test_high_volume_bash_output.py).
"""

import asyncio
import statistics
import time
from uuid import UUID

import pytest

from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import Agent
from openhands.sdk.workspace import LocalWorkspace
from tests.agent_server.stress.budgets import EVENT_LOOP_RESPONSIVENESS
from tests.agent_server.stress.scripts import placeholder_llm


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

        events_resp = await client.get(
            "/api/bash/bash_events/search",
            params={"command_id__eq": str(cmd_id)},
        )
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
