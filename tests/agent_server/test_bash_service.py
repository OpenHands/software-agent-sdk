"""Tests for bash_service.py."""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from openhands.agent_server import bash_router as bash_router_module
from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.config import Config
from openhands.agent_server.models import BashCommand, BashEventSortOrder, BashOutput
from openhands.agent_server.server_details_router import (
    mark_initialization_complete,
    server_details_router,
)


@pytest_asyncio.fixture
async def bash_service(tmp_path: Path) -> AsyncIterator[BashEventService]:
    service = BashEventService(bash_events_dir=tmp_path / "bash_events")
    async with service:
        yield service


@pytest_asyncio.fixture
async def client(bash_service: BashEventService) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.config = Config()
    app.state.bash_event_service = bash_service
    app.include_router(server_details_router)
    app.include_router(bash_router_module.bash_router, prefix="/api")
    mark_initialization_complete()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.mark.timeout(30)
async def test_bash_timeout_runs_sigterm_trap(
    client: httpx.AsyncClient,
    bash_service: BashEventService,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    marker = tmp_path / "cleanup_ran"
    secret = "ghp_" + "a" * 36
    caplog.set_level(logging.DEBUG)
    resp = await client.post(
        "/api/bash/start_bash_command",
        json={
            "command": (
                f"LEAK_TEST={secret}; trap 'touch {marker}; exit 0' TERM; sleep 30"
            ),
            "timeout": 1,
        },
    )
    assert resp.status_code == 200, resp.text
    cmd_id = UUID(resp.json()["id"])

    # Wait for the timeout to fire and the process to be reaped.
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        items = (
            await client.get(
                "/api/bash/bash_events/search",
                params={"command_id__eq": str(cmd_id)},
            )
        ).json()["items"]
        if any(
            e["kind"] == "BashOutput" and e.get("exit_code") is not None for e in items
        ):
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail(f"command {cmd_id} did not finish")

    await asyncio.sleep(0.2)  # let the trap's filesystem write land
    assert marker.exists(), "SIGTERM trap did not run; cleanup skipped."
    assert "Command timed out" in caplog.text
    assert secret not in caplog.text


async def test_bash_execution_error_log_omits_command(
    bash_service: BashEventService,
    caplog: pytest.LogCaptureFixture,
):
    secret = "ghp_" + "e" * 36
    caplog.set_level(logging.DEBUG)
    command = BashCommand(command=f"printf '{secret}'")
    failure = RuntimeError(f"failed to start command containing {secret}")

    with patch(
        "openhands.agent_server.bash_service.asyncio.create_subprocess_shell",
        new=AsyncMock(side_effect=failure),
    ):
        await bash_service._execute_bash_command(command)

    assert "Error executing bash command" in caplog.text
    assert secret not in caplog.text


# ---------------------------------------------------------------------------
# search_bash_events
# ---------------------------------------------------------------------------


async def test_search_bash_events_filters_and_paginates(tmp_path: Path):
    service = BashEventService(bash_events_dir=tmp_path / "bash_events")
    command_id = uuid4()
    other_command_id = uuid4()

    command = BashCommand(command="echo target", id=command_id, timestamp=_OLD)
    output_0 = BashOutput(
        command_id=command_id,
        order=0,
        stdout="first",
        timestamp=_OLD.replace(microsecond=1),
    )
    output_1 = BashOutput(
        command_id=command_id,
        order=1,
        stdout="second",
        timestamp=_OLD.replace(microsecond=2),
    )
    other_output = BashOutput(
        command_id=other_command_id,
        order=0,
        stdout="other",
        timestamp=_OLD.replace(microsecond=3),
    )
    for event in (command, output_0, output_1, other_output):
        service._save_event_to_file(event)

    first_page = await service.search_bash_events(
        kind__eq="BashOutput",
        command_id__eq=command_id,
        sort_order=BashEventSortOrder.TIMESTAMP,
        limit=1,
    )
    assert [event.id for event in first_page.items] == [output_0.id]
    assert first_page.next_page_id is not None

    second_page = await service.search_bash_events(
        kind__eq="BashOutput",
        command_id__eq=command_id,
        sort_order=BashEventSortOrder.TIMESTAMP,
        page_id=first_page.next_page_id,
        limit=10,
    )
    assert [event.id for event in second_page.items] == [output_1.id]
    assert second_page.next_page_id is None


async def test_search_bash_events_runs_blocking_scan_off_event_loop(
    tmp_path: Path,
):
    service = BashEventService(bash_events_dir=tmp_path / "bash_events")
    service._save_event_to_file(BashCommand(command="echo ok"))

    with patch(
        "openhands.agent_server.bash_service.asyncio.to_thread",
        wraps=asyncio.to_thread,
    ) as to_thread:
        page = await service.search_bash_events(limit=10)

    assert len(page.items) == 1
    assert to_thread.call_count == 1
    assert to_thread.call_args.args[0] == service._search_bash_events_sync


async def test_get_bash_event_runs_blocking_scan_off_event_loop(tmp_path: Path):
    service = BashEventService(bash_events_dir=tmp_path / "bash_events")
    command = BashCommand(command="echo ok")
    service._save_event_to_file(command)

    with patch(
        "openhands.agent_server.bash_service.asyncio.to_thread",
        wraps=asyncio.to_thread,
    ) as to_thread:
        event = await service.get_bash_event(command.id.hex)

    assert event is not None
    assert event.id == command.id
    assert to_thread.call_count == 1
    assert to_thread.call_args.args[0] == service._get_bash_event_sync


async def test_concurrent_searches_serialize_filesystem_scans(tmp_path: Path):
    service = BashEventService(bash_events_dir=tmp_path / "bash_events")
    service._save_event_to_file(BashCommand(command="echo ok"))

    active = 0
    max_active = 0
    original_search = service._search_bash_events_sync

    def tracked_search(*args):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            # Keep the worker occupied long enough for all concurrent callers
            # to queue on the async semaphore.
            time.sleep(0.02)
            return original_search(*args)
        finally:
            active -= 1

    with patch.object(service, "_search_bash_events_sync", tracked_search):
        pages = await asyncio.gather(
            *(service.search_bash_events(limit=10) for _ in range(8))
        )

    assert all(len(page.items) == 1 for page in pages)
    assert max_active == 1


# ---------------------------------------------------------------------------
# delete_events_older_than
# ---------------------------------------------------------------------------

_OLD = datetime(2020, 1, 1, tzinfo=UTC)
_NEW = datetime(2022, 1, 1, tzinfo=UTC)
_CUTOFF = datetime(2021, 1, 1, tzinfo=UTC)


def test_delete_events_older_than_removes_old_keeps_new(tmp_path: Path):
    service = BashEventService(bash_events_dir=tmp_path / "bash_events")

    old_cmd = BashCommand(command="echo old", timestamp=_OLD)
    new_cmd = BashCommand(command="echo new", timestamp=_NEW)
    service._save_event_to_file(old_cmd)
    service._save_event_to_file(new_cmd)

    count = service.delete_events_older_than(_CUTOFF)

    assert count == 1
    remaining = service._get_event_files_by_pattern("*")
    assert len(remaining) == 1
    assert new_cmd.id.hex in remaining[0].name


def test_delete_events_older_than_empty_directory(tmp_path: Path):
    service = BashEventService(bash_events_dir=tmp_path / "bash_events")
    count = service.delete_events_older_than(_CUTOFF)
    assert count == 0


def test_delete_events_older_than_all_newer_are_skipped(tmp_path: Path):
    service = BashEventService(bash_events_dir=tmp_path / "bash_events")

    new_cmd = BashCommand(command="echo new", timestamp=_NEW)
    service._save_event_to_file(new_cmd)

    count = service.delete_events_older_than(_CUTOFF)

    assert count == 0
    assert len(service._get_event_files_by_pattern("*")) == 1


def test_delete_events_older_than_returns_correct_count(tmp_path: Path):
    service = BashEventService(bash_events_dir=tmp_path / "bash_events")

    for i in range(3):
        service._save_event_to_file(BashCommand(command=f"echo {i}", timestamp=_OLD))
    service._save_event_to_file(BashCommand(command="echo new", timestamp=_NEW))

    count = service.delete_events_older_than(_CUTOFF)

    assert count == 3
    assert len(service._get_event_files_by_pattern("*")) == 1


# ---------------------------------------------------------------------------
# run_retention_cleanup_loop
# ---------------------------------------------------------------------------


@pytest.mark.timeout(5)
async def test_run_retention_cleanup_loop_purges_old_events(tmp_path: Path):
    service = BashEventService(bash_events_dir=tmp_path / "bash_events")

    # Write an event whose recorded timestamp is well in the past.
    service._save_event_to_file(BashCommand(command="echo old", timestamp=_OLD))
    assert len(service._get_event_files_by_pattern("*")) == 1

    # Run the loop with a 1-second retention window and a 50 ms tick so
    # the test doesn't have to wait for the default 60-second interval.
    task = asyncio.create_task(
        service.run_retention_cleanup_loop(retention_seconds=1, interval_seconds=0.05)
    )
    try:
        # Give the loop time to fire at least once.
        await asyncio.sleep(0.15)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert len(service._get_event_files_by_pattern("*")) == 0, (
        "Old event file should have been purged by the retention loop"
    )
