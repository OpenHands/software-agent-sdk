"""Tests for bash_service.py."""

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from openhands.agent_server import bash_router as bash_router_module
from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.config import Config
from openhands.agent_server.models import BashCommand
from openhands.agent_server.server_details_router import (
    mark_initialization_complete,
    server_details_router,
)


@pytest_asyncio.fixture
async def bash_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[BashEventService]:
    service = BashEventService(bash_events_dir=tmp_path / "bash_events")
    async with service:
        # bash_router holds its service as a module-level global; swap it.
        monkeypatch.setattr(bash_router_module, "bash_event_service", service)
        yield service


@pytest_asyncio.fixture
async def client(bash_service: BashEventService) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.config = Config()
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
):
    marker = tmp_path / "cleanup_ran"
    resp = await client.post(
        "/api/bash/start_bash_command",
        json={
            "command": f"trap 'touch {marker}; exit 0' TERM; sleep 30",
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
