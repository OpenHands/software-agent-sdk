"""Stress test: listing many conversations.

Bug class this catches:
    - O(N) listing where pagination should be O(page_size).
    - Pagination off-by-one or duplication.
    - Accidental global locks held during list (would serialize concurrent
      list calls and inflate p95).
    - Per-call leaks: listing N times shouldn't grow RSS proportionally.

Why N=2000 and not 10k:
    Going through start_conversation 10k times takes minutes; loading them
    through ``ConversationService.__aenter__`` after that takes minutes
    again. N=2000 still surfaces O(N) regressions strongly while keeping
    the test under a minute.
"""

import asyncio
import statistics
import time
from uuid import UUID

import pytest

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import Agent
from openhands.sdk.workspace import LocalWorkspace
from tests.agent_server.stress.budgets import CONVERSATION_LISTING
from tests.agent_server.stress.probe import ResourceProbe
from tests.agent_server.stress.scripts import placeholder_llm


pytestmark = pytest.mark.stress


async def _seed_conversations(
    conversation_service: ConversationService,
    *,
    n: int,
    workspace_dir: str,
) -> set[UUID]:
    """Seed n conversations through the public service path.

    Concurrency=8 is enough to amortize the per-conversation fixed cost
    without overwhelming the lease layer. We use the placeholder LLM and
    autotitle=False so seeding never hits the network.
    """
    semaphore = asyncio.Semaphore(8)

    async def _one(i: int) -> UUID:
        async with semaphore:
            # No initial_message: start_conversation would otherwise call
            # event_service.send_message(..., run_after_send=True), which
            # invokes the placeholder LLM and fails with a real auth error.
            # We only need the persistence row to exist for listing.
            request = StartConversationRequest(
                agent=Agent(llm=placeholder_llm(f"seed-{i}"), tools=[]),
                workspace=LocalWorkspace(working_dir=workspace_dir),
                autotitle=False,
            )
            info, _ = await conversation_service.start_conversation(request)
            return info.id

    ids = await asyncio.gather(*[_one(i) for i in range(n)])
    return set(ids)


_MAX_PAGINATION_ITERATIONS = 10_000


async def _walk_pages(
    client, *, page_size: int, sort_order: str
) -> list[tuple[UUID, str]]:
    """Walk every page of /api/conversations/search.

    Returns ``(id, created_at)`` pairs in API-returned order. ``created_at``
    is the raw ISO string from the response; callers compare it pairwise to
    verify ``sort_order`` was actually honoured. UTC-only timestamps make
    lexicographic comparison equivalent to chronological.
    """
    seen: list[tuple[UUID, str]] = []
    page_id: str | None = None
    # No `pytest.mark.timeout` on this file, so a circular `next_page_id`
    # would otherwise hang indefinitely. At N=2000 / limit=50 we expect
    # ~40 iterations; 10k is a 250× safety margin.
    for _ in range(_MAX_PAGINATION_ITERATIONS):
        params: dict[str, object] = {
            "limit": page_size,
            "sort_order": sort_order,
        }
        if page_id is not None:
            params["page_id"] = page_id
        resp = await client.get("/api/conversations/search", params=params)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        for item in body["items"]:
            seen.append((UUID(item["id"]), item["created_at"]))
        page_id = body.get("next_page_id")
        if not page_id:
            return seen
    raise AssertionError(
        f"pagination did not terminate in {_MAX_PAGINATION_ITERATIONS} "
        f"iterations — possible circular next_page_id."
    )


async def _find_last_page_id(client, *, page_size: int, sort_order: str) -> str | None:
    """Return the page_id cursor for the final page, or None if pagination
    fits in a single page."""
    page_id: str | None = None
    for _ in range(_MAX_PAGINATION_ITERATIONS):
        params: dict[str, object] = {"limit": page_size, "sort_order": sort_order}
        if page_id is not None:
            params["page_id"] = page_id
        resp = await client.get("/api/conversations/search", params=params)
        assert resp.status_code == 200, resp.text
        next_id = resp.json().get("next_page_id")
        if not next_id:
            return page_id
        page_id = next_id
    raise AssertionError(
        f"pagination did not terminate in {_MAX_PAGINATION_ITERATIONS} "
        f"iterations — possible circular next_page_id."
    )


async def _time_first_page(client, *, page_size: int) -> float:
    t0 = time.monotonic()
    resp = await client.get(
        "/api/conversations/search",
        params={"limit": page_size, "sort_order": "CREATED_AT_DESC"},
    )
    assert resp.status_code == 200
    return time.monotonic() - t0


async def _time_deep_page(client, *, page_size: int, page_id: str) -> float:
    t0 = time.monotonic()
    resp = await client.get(
        "/api/conversations/search",
        params={
            "limit": page_size,
            "sort_order": "CREATED_AT_DESC",
            "page_id": page_id,
        },
    )
    assert resp.status_code == 200
    return time.monotonic() - t0


async def test_pagination_is_correct_and_bounded(
    conversation_service: ConversationService,
    client,
    tmp_path,
    probe: ResourceProbe,
):
    """Seed N, walk pages, assert correctness + latency + memory bounds."""
    n = CONVERSATION_LISTING.n_conversations
    page_size = CONVERSATION_LISTING.page_size
    workspace = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir()

    seeded = await _seed_conversations(
        conversation_service, n=n, workspace_dir=workspace
    )
    assert len(seeded) == n, "seeding hit a UUID collision (cosmically unlikely)"

    # 1. Correctness: paginated set == seeded set, no duplicates.
    paged = await _walk_pages(client, page_size=page_size, sort_order="CREATED_AT_DESC")
    paged_ids = [u for u, _ in paged]
    assert len(paged_ids) == n, (
        f"pagination returned {len(paged_ids)} items, seeded {n}. "
        f"Duplicates or missing pages?"
    )
    assert set(paged_ids) == seeded, (
        "pagination returned a different set than was seeded. "
        f"Diff: missing={seeded - set(paged_ids)}, "
        f"extra={set(paged_ids) - seeded}."
    )

    # 1b. Sort order: CREATED_AT_DESC must actually be descending. Without
    # this, a regression that ignores sort_order would still pass set/count
    # checks. created_at strings are UTC ISO so lexicographic == chronological.
    timestamps = [t for _, t in paged]
    first_break = next(
        (i for i in range(len(timestamps) - 1) if timestamps[i] < timestamps[i + 1]),
        -1,
    )
    assert first_break == -1, (
        f"CREATED_AT_DESC did not return items in descending order. "
        f"First disagreement at index {first_break}: "
        f"{timestamps[first_break]} < {timestamps[first_break + 1]}."
    )

    # 1c. Sort order: CREATED_AT (ASC) must actually be ascending. Together
    # with 1b above, this catches a regression that ignores sort_order and
    # always returns one fixed direction (which 1b alone wouldn't notice).
    paged_asc = await _walk_pages(client, page_size=page_size, sort_order="CREATED_AT")
    timestamps_asc = [t for _, t in paged_asc]
    first_break_asc = next(
        (
            i
            for i in range(len(timestamps_asc) - 1)
            if timestamps_asc[i] > timestamps_asc[i + 1]
        ),
        -1,
    )
    assert first_break_asc == -1, (
        f"CREATED_AT did not return items in ascending order. "
        f"First disagreement at index {first_break_asc}: "
        f"{timestamps_asc[first_break_asc]} > {timestamps_asc[first_break_asc + 1]}."
    )

    # 2. Count endpoint matches.
    count_resp = await client.get("/api/conversations/count")
    assert count_resp.status_code == 200
    assert count_resp.json() == n

    # 3. First-page latency budget.
    first_page_samples = [
        await _time_first_page(client, page_size=page_size) for _ in range(10)
    ]
    p95_first = statistics.quantiles(first_page_samples, n=20)[-1]
    assert p95_first < CONVERSATION_LISTING.p95_first_page_s, (
        f"first-page p95 {p95_first:.3f}s > budget "
        f"{CONVERSATION_LISTING.p95_first_page_s}s. Listing has likely gone "
        f"O(N)."
    )

    # 4. Deep-page latency degradation: should be graceful, not a cliff.
    # With N=2000 and page_size=50 we expect ~40 pages, so _find_last_page_id
    # must return a non-None cursor. None here means the API returned
    # everything in one page (pagination broken) — assert loudly so the
    # deep-page block doesn't silently no-op.
    deep_page_id = await _find_last_page_id(
        client, page_size=page_size, sort_order="CREATED_AT_DESC"
    )
    assert deep_page_id is not None, (
        f"expected multi-page pagination for N={n} with page_size={page_size}, "
        f"but the API returned everything in one page. Pagination is broken."
    )
    deep_samples = [
        await _time_deep_page(client, page_size=page_size, page_id=deep_page_id)
        for _ in range(10)
    ]
    p95_deep = statistics.quantiles(deep_samples, n=20)[-1]
    ratio = p95_deep / max(p95_first, 1e-6)
    assert ratio < CONVERSATION_LISTING.deep_page_factor, (
        f"deep-page p95 ({p95_deep:.3f}s) is {ratio:.1f}× first-page "
        f"({p95_first:.3f}s). Pagination likely re-scans from the start each "
        f"call."
    )

    # 5. RSS during a tight listing loop. Per-call slope is too noisy
    #    in-process (allocator behaviour, fragmentation), so we measure
    #    listing-start vs peak-during-listing. A "list everything into
    #    memory each call" regression overruns this; allocator noise does
    #    not.
    #
    # Use only samples captured during the loop — `probe.peak_rss_mb()`
    # returns the all-time peak, which would include the seeding spike from
    # earlier in the test and inflate the delta artificially.
    pre_loop_idx = len(probe.samples)
    assert pre_loop_idx > 0, "ResourceProbe yielded no samples — fixture not entered?"
    pre_loop_rss = probe.samples[-1].rss_mb
    for _k in range(50):
        await _time_first_page(client, page_size=page_size)
    peak_during_loop = max(
        (s.rss_mb for s in probe.samples[pre_loop_idx:]),
        default=pre_loop_rss,
    )
    delta = peak_during_loop - pre_loop_rss
    assert delta < CONVERSATION_LISTING.listing_rss_delta_mb, (
        f"RSS grew {delta:.1f} MB during 50 list calls "
        f"({pre_loop_rss:.1f} → peak {peak_during_loop:.1f} MB; budget "
        f"{CONVERSATION_LISTING.listing_rss_delta_mb} MB). The listing path "
        f"may be materializing the full store into memory per call."
    )
