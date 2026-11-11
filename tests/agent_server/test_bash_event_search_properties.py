"""Property test for paging through BashEventService.search_bash_events.

For any saved bash events and any combination of search arguments, following
``next_page_id`` until it is None returns exactly the events that a plain
filter-then-sort over the same events selects, in order and each once, and
every page but the last holds exactly ``limit`` items. Timestamp bounds select
the same events whatever UTC offset they are given in, or none (read as UTC).
"""

import asyncio
import tempfile
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from hypothesis import given, strategies as st

from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.models import BashCommand, BashEventSortOrder, BashOutput


BASE_TIME = datetime(2021, 6, 1, 10, tzinfo=UTC)
COMMAND_IDS = [UUID(int=1), UUID(int=2)]

# A few seconds at microsecond resolution, so events often share a second.
TIMESTAMPS = st.integers(0, 3_000_000).map(
    lambda offset: BASE_TIME + timedelta(microseconds=offset)
)
BASH_COMMANDS = st.builds(
    BashCommand, id=st.uuids(), command=st.just("x"), timestamp=TIMESTAMPS
)
BASH_OUTPUTS = st.builds(
    BashOutput,
    id=st.uuids(),
    command_id=st.sampled_from(COMMAND_IDS),
    order=st.integers(0, 4),
    stdout=st.just("x"),
    timestamp=TIMESTAMPS,
)
# Distinct timestamps: the relative order of simultaneous events is unspecified.
EVENTS = st.lists(
    BASH_COMMANDS | BASH_OUTPUTS,
    max_size=10,
    unique_by=(lambda e: e.timestamp, lambda e: e.id),
)
UTC_OFFSETS = st.integers(-12 * 60, 14 * 60).map(
    lambda minutes: timezone(timedelta(minutes=minutes))
)


def spelled_in(bound: datetime | None, tz: timezone | None) -> datetime | None:
    if bound is None:
        return None
    if tz is None:
        return bound.astimezone(UTC).replace(tzinfo=None)
    return bound.astimezone(tz)


@given(
    events=EVENTS,
    kind=st.sampled_from([None, "BashCommand", "BashOutput"]),
    command_id=st.sampled_from([None, *COMMAND_IDS]),
    gte=st.none() | TIMESTAMPS,
    lt=st.none() | TIMESTAMPS,
    bound_tz=st.none() | UTC_OFFSETS,
    order_gt=st.none() | st.integers(-1, 4),
    sort_order=st.sampled_from(BashEventSortOrder),
    limit=st.integers(1, 4),
)
def test_paged_search_returns_filtered_sorted_events(
    events: list[BashCommand | BashOutput],
    kind: str | None,
    command_id: UUID | None,
    gte: datetime | None,
    lt: datetime | None,
    bound_tz: timezone | None,
    order_gt: int | None,
    sort_order: BashEventSortOrder,
    limit: int,
) -> None:
    def selected(event: BashCommand | BashOutput) -> bool:
        if kind is not None and event.kind != kind:
            return False
        if command_id is not None and not (
            isinstance(event, BashOutput) and event.command_id == command_id
        ):
            return False
        if gte is not None and event.timestamp < gte:
            return False
        if lt is not None and event.timestamp >= lt:
            return False
        if order_gt is not None and isinstance(event, BashOutput):
            return event.order > order_gt
        return True

    expected = sorted(
        (e for e in events if selected(e)),
        key=lambda e: e.timestamp,
        reverse=sort_order == BashEventSortOrder.TIMESTAMP_DESC,
    )

    async def page_through(service: BashEventService) -> list[UUID]:
        collected: list[UUID] = []
        page_id: str | None = None
        for _ in range(len(events) + 2):
            page = await service.search_bash_events(
                kind__eq=kind,
                command_id__eq=command_id,
                timestamp__gte=spelled_in(gte, bound_tz),
                timestamp__lt=spelled_in(lt, bound_tz),
                order__gt=order_gt,
                sort_order=sort_order,
                page_id=page_id,
                limit=limit,
            )
            assert len(page.items) <= limit
            assert page.next_page_id is None or len(page.items) == limit
            collected += [e.id for e in page.items]
            page_id = page.next_page_id
            if page_id is None:
                return collected
        raise AssertionError(f"next_page_id never became None after {collected}")

    with tempfile.TemporaryDirectory() as workdir:
        service = BashEventService(bash_events_dir=Path(workdir) / "bash_events")
        for event in events:
            service._save_event_to_file(event)
        collected = asyncio.run(page_through(service))

    assert collected == [e.id for e in expected]
