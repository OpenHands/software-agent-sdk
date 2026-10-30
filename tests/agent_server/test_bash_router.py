"""Tests for the synchronous ``execute_bash_command`` route."""

import httpx
import pytest
from fastapi import FastAPI

import openhands.agent_server.bash_service as bash_service_module
from openhands.agent_server.bash_router import bash_router
from openhands.agent_server.bash_service import BashEventService


@pytest.fixture
async def bash_client(tmp_path):
    app = FastAPI()
    app.include_router(bash_router, prefix="/api")
    app.state.bash_event_service = BashEventService(tmp_path / "bash_events")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def _execute(client: httpx.AsyncClient, command: str) -> dict:
    response = await client.post(
        "/api/bash/execute_bash_command",
        json={"command": command, "timeout": 60},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _stored_outputs(client: httpx.AsyncClient, command_id: str) -> int:
    count, page_id = 0, None
    while True:
        params = {"kind__eq": "BashOutput", "command_id__eq": command_id}
        if page_id:
            params["page_id"] = page_id
        page = (await client.get("/api/bash/bash_events/search", params=params)).json()
        count += len(page["items"])
        page_id = page["next_page_id"]
        if not page_id:
            return count


@pytest.mark.asyncio
async def test_execute_returns_all_chunks_of_large_output(bash_client, monkeypatch):
    monkeypatch.setattr(bash_service_module, "MAX_CONTENT_CHAR_LENGTH", 1000)
    expected = "a\n" * 1500 + "TAIL\n"

    result = await _execute(
        bash_client, "yes a | head -n 1500; echo TAIL; echo oops >&2"
    )

    assert await _stored_outputs(bash_client, result["command_id"]) > 1
    assert result["stdout"] == expected
    assert result["stderr"] == "oops\n"
    assert result["exit_code"] == 0


@pytest.mark.asyncio
async def test_execute_reports_exit_code_past_one_search_page(bash_client, monkeypatch):
    monkeypatch.setattr(bash_service_module, "MAX_CONTENT_CHAR_LENGTH", 10)
    # 2 * 600 = 1200 characters in 10-character chunks: over 100 stored events.
    result = await _execute(bash_client, "yes a | head -n 600; exit 7")

    assert await _stored_outputs(bash_client, result["command_id"]) > 100
    assert result["stdout"] == "a\n" * 600
    assert result["exit_code"] == 7


@pytest.mark.asyncio
async def test_execute_small_output_is_unchanged(bash_client):
    result = await _execute(bash_client, "printf hi")

    assert result["stdout"] == "hi"
    assert result["stderr"] is None
    assert result["exit_code"] == 0
    assert result["order"] == 0

    empty = await _execute(bash_client, "exit 3")
    assert empty["stdout"] is None
    assert empty["stderr"] is None
    assert empty["exit_code"] == 3


@pytest.fixture
async def scoped_bash_client(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "sub").mkdir(parents=True)
    app = FastAPI()
    app.include_router(bash_router, prefix="/api")
    app.state.bash_event_service = BashEventService(
        tmp_path / "bash_events", default_cwd=str(workspace)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, workspace


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["start_bash_command", "execute_bash_command"])
async def test_cwd_escaping_the_workspace_is_rejected(
    scoped_bash_client, tmp_path, route
):
    client, workspace = scoped_bash_client
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "escape").symlink_to(outside, target_is_directory=True)

    for cwd in (str(outside), f"{workspace}/../outside", str(workspace / "escape")):
        response = await client.post(
            f"/api/bash/{route}", json={"command": "pwd", "cwd": cwd}
        )
        assert response.status_code == 422, cwd


@pytest.mark.asyncio
async def test_cwd_inside_the_workspace_is_accepted(scoped_bash_client):
    client, workspace = scoped_bash_client

    response = await client.post(
        "/api/bash/execute_bash_command",
        json={"command": "pwd -P", "cwd": str(workspace / "sub")},
    )

    assert response.status_code == 200
    assert response.json()["stdout"].strip() == str((workspace / "sub").resolve())


@pytest.mark.asyncio
async def test_get_unknown_bash_event_returns_404(bash_client):
    response = await bash_client.get("/api/bash/bash_events/" + "0" * 32)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_stop_unknown_command_returns_404(bash_client):
    response = await bash_client.post("/api/bash/bash_commands/" + "0" * 32 + "/stop")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_stop_finished_command_is_idempotent(bash_client):
    result = await _execute(bash_client, "true")

    response = await bash_client.post(
        f"/api/bash/bash_commands/{result['command_id']}/stop"
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_search_rejects_unknown_kind(bash_client):
    response = await bash_client.get(
        "/api/bash/bash_events/search", params={"kind__eq": "Nope"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_execute_rejects_request_without_command(bash_client):
    response = await bash_client.post("/api/bash/execute_bash_command", json={})
    assert response.status_code == 422
