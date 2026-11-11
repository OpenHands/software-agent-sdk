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
