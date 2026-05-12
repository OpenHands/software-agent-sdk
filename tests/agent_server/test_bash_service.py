"""Tests for bash_service.py."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from openhands.agent_server import bash_router as bash_router_module
from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.config import Config
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
