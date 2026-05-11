"""Stress test: high-volume bash output must be coalesced, not 1-event-per-byte.

Bug class this catches:
    - Per-byte / per-line BashOutput event creation that O(N²)s under
      `yes`-style rapid output.
    - Server unresponsiveness while bash floods the executor.
    - Bash event store growing without bound.

What "coalesced" means in this codebase:
    bash_service.MAX_CONTENT_CHAR_LENGTH is 1 MiB (1024*1024). BashOutput
    is emitted when the buffer crosses that threshold or at command end.
    So a 5 MiB `yes` flood produces ~5–6 events, not thousands.
"""

import asyncio
import os
import statistics
import time
from uuid import UUID

import pytest

from openhands.agent_server.bash_service import BashEventService
from tests.agent_server.stress.budgets import HIGH_VOLUME_BASH_OUTPUT
from tests.agent_server.stress.scripts import descendants_of


pytestmark = [pytest.mark.stress, pytest.mark.timeout(60)]


async def test_high_volume_bash_output_is_bounded(
    client,
    bash_service: BashEventService,
):
    """Run a fast-emitting command; assert event count is bounded and
    /health stays responsive throughout."""
    duration = HIGH_VOLUME_BASH_OUTPUT.duration_s

    # `yes | head -c <bytes>` emits a known-size flood quickly; coupling to
    # a deterministic byte count makes the event-count assertion stable
    # across machines (a wall-clock-bounded `yes` produces variable output).
    flood_bytes = 5 * 1024 * 1024  # 5 MB
    pre_children = set(p.pid for p in descendants_of(os.getpid()))
    resp = await client.post(
        "/api/bash/start_bash_command",
        json={
            "command": f"yes | head -c {flood_bytes}",
            "timeout": int(duration + 5),
        },
    )
    assert resp.status_code == 200, resp.text
    cmd_id = UUID(resp.json()["id"])

    # While the flood runs, sample /health latency.
    health_lats: list[float] = []
    flood_deadline = time.monotonic() + duration + 5
    while time.monotonic() < flood_deadline:
        # `limit=1, sort_order=TIMESTAMP_DESC` fetches only the latest
        # event. The default page caps at 100; this test deliberately
        # generates output that *could* exceed that under a per-byte/
        # per-line regression, so a first-page fetch would miss the
        # final BashOutput and the loop would time out for the wrong
        # reason. The dedicated event-count assertion below paginates
        # explicitly to catch the underlying regression.
        events_resp = await client.get(
            "/api/bash/bash_events/search",
            params={
                "command_id__eq": str(cmd_id),
                "limit": 1,
                "sort_order": "TIMESTAMP_DESC",
            },
        )
        items = events_resp.json()["items"]
        final = next(
            (
                e
                for e in items
                if e["kind"] == "BashOutput" and e.get("exit_code") is not None
            ),
            None,
        )

        # Hammer health a few times per loop iteration.
        for _ in range(5):
            t0 = time.monotonic()
            h_resp = await client.get("/health")
            health_lats.append(time.monotonic() - t0)
            assert h_resp.status_code == 200

        if final is not None:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("yes flood did not terminate within budget")

    # Count all events for this command. The search endpoint caps each page
    # at 100, so a single fetch can't tell us anything above 100 — we have
    # to paginate or we'd silently treat ">100 events" as "exactly 100".
    total_events = 0
    page_id: str | None = None
    while True:
        params: dict[str, object] = {
            "command_id__eq": str(cmd_id),
            "limit": 100,
        }
        if page_id is not None:
            params["page_id"] = page_id
        page = (await client.get("/api/bash/bash_events/search", params=params)).json()
        total_events += len(page["items"])
        page_id = page.get("next_page_id")
        if not page_id:
            break

    # 1. Event count bounded. With 1 MiB buffer-based coalescing, a 5 MiB
    #    flood produces ~5–6 BashOutput events plus 1 BashCommand. Per-line
    #    emission would explode this to millions.
    assert total_events < HIGH_VOLUME_BASH_OUTPUT.max_events, (
        f"bash flood produced {total_events} events for "
        f"{flood_bytes} bytes (budget < {HIGH_VOLUME_BASH_OUTPUT.max_events}). "
        f"Output is being emitted per-line/per-byte instead of coalesced."
    )

    # 2. /health stayed responsive throughout. Require ≥ 10 samples so the
    # n=20 quantile actually represents a p95 — with fewer samples it
    # collapses toward the max and the assertion stops being meaningful.
    assert len(health_lats) >= 10, (
        f"only {len(health_lats)} /health samples collected during the "
        f"flood; not enough for a representative p95. Either the flood "
        f"finished before sampling could land or the polling loop is "
        f"misconfigured."
    )
    p95 = statistics.quantiles(health_lats, n=20)[-1]
    assert p95 < HIGH_VOLUME_BASH_OUTPUT.health_p95_s, (
        f"/health p95 {p95 * 1000:.1f} ms during bash flood (budget "
        f"{HIGH_VOLUME_BASH_OUTPUT.health_p95_s * 1000:.0f} ms). The "
        f"flood is starving the event loop."
    )

    # 3. Pipeline cleanup: `yes | head -c N` is two processes (the shell
    # spawns yes, head, and a writer). After the command completes, all
    # descendants must be reaped — bash_service mishandling process groups
    # for pipelines would leak children that test_long_running_command
    # doesn't surface (it only exercises non-pipeline shells).
    cleanup_deadline = time.monotonic() + 3.0
    leaked: set[int] = set()
    while time.monotonic() < cleanup_deadline:
        leaked = set(p.pid for p in descendants_of(os.getpid())) - pre_children
        if not leaked:
            break
        await asyncio.sleep(0.1)
    assert not leaked, (
        f"after the flood ended, descendants of the test process still "
        f"include {leaked}. bash_service is leaking pipeline children."
    )
