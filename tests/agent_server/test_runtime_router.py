import asyncio
import os
import sys
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

import openhands.agent_server.vscode_service as vscode_service_module
from openhands.agent_server.api import create_app
from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.runtime_router import (
    add_legacy_runtime_routes,
    create_runtime_router,
)
from openhands.agent_server.vscode_service import VSCodeService


@pytest.fixture
async def runtime_client(tmp_path):
    config = Config(
        conversations_path=tmp_path / "conversations",
        session_api_keys=["runtime-route-test-key"],
    )
    app = create_app(config)
    async with ConversationService(
        conversations_dir=config.conversations_path
    ) as service:
        app.state.conversation_service = service
        app.state.bash_event_service = BashEventService(tmp_path / "global-bash")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Session-API-Key": "runtime-route-test-key"},
        ) as client:
            yield client, app


async def _create(client, directory):
    response = await client.post(
        "/api/conversations",
        json={
            "workspace": {"working_dir": str(directory)},
            "agent": {
                "kind": "Agent",
                "llm": {"model": "openai/test", "usage_id": "test"},
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.asyncio
async def test_local_runtime_workspace_and_terminal_context(runtime_client, tmp_path):
    client, _ = runtime_client
    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    first = await _create(client, first_dir)
    second = await _create(client, second_dir)
    prefix = f"/api/conversations/{first}"
    other = f"/api/conversations/{second}"
    result = await client.post(
        prefix + "/bash/execute_bash_command",
        json={"command": "pwd; printf first > marker"},
    )
    assert result.status_code == 200, result.text
    assert str(first_dir) in result.json()["stdout"]
    assert (first_dir / "marker").read_text() == "first"
    assert not (second_dir / "marker").exists()
    history = await client.get(other + "/bash/bash_events/search")
    assert history.json()["items"] == []
    wrong_cwd = await client.post(
        other + "/bash/execute_bash_command",
        json={"command": "pwd", "cwd": str(first_dir)},
    )
    assert wrong_cwd.status_code == 422
    own_file = await client.get(
        prefix + "/file/download", params={"path": str(first_dir / "marker")}
    )
    assert own_file.text == "first"
    wrong_file = await client.get(
        other + "/file/download", params={"path": str(first_dir / "marker")}
    )
    assert wrong_file.status_code == 422
    legacy = await client.get(
        "/api/file/download", params={"path": str(first_dir / "marker")}
    )
    assert legacy.text == "first"
    missing = await client.get(
        f"/api/conversations/{uuid4()}/git/changes", params={"path": str(first_dir)}
    )
    assert missing.status_code == 404
    unauthorized = await client.get(
        prefix + "/bash/bash_events/search", headers={"X-Session-API-Key": "wrong"}
    )
    assert unauthorized.status_code == 401
    info = (await client.get("/server_info")).json()
    assert info["workspace_mode"] == "host"
    assert info["conversation_runtime"] == "local"
    assert "conversation_runtime_routes_v1" in info["capabilities"]


def test_canonical_runtime_openapi_preserves_methods_and_schemas():
    schema = create_app(Config()).openapi()
    paths = schema["paths"]
    for resource, method, endpoint in [
        ("bash", "post", "execute_bash_command"),
        ("file", "get", "download"),
        ("git", "get", "changes"),
        ("desktop", "get", "url"),
        ("vscode", "get", "url"),
        ("mcp", "post", "test"),
    ]:
        operation = paths[
            f"/api/conversations/{{runtime_conversation_id}}/{resource}/{endpoint}"
        ][method]
        assert any(
            p["in"] == "path" and p["name"] == "runtime_conversation_id"
            for p in operation["parameters"]
        )
        assert operation["responses"]["200"]
    assert not any("runtime_conversation_id}/mcp/oauth" in path for path in paths)
    for endpoint in ("download", "archive", "download-trajectory/{conversation_id}"):
        content = paths[
            f"/api/conversations/{{runtime_conversation_id}}/file/{endpoint}"
        ]["get"]["responses"]["200"]["content"]
        assert "application/json" not in content
        assert content["application/octet-stream"]["schema"] == {
            "type": "string",
            "format": "binary",
        }


@pytest.mark.asyncio
async def test_mcp_probe_scope_and_workspace_validation(runtime_client, tmp_path):
    client, _ = runtime_client
    root = tmp_path / "workspace"
    cid = await _create(client, root)
    script = root / "mcp_server.py"
    script.write_text(
        "from fastmcp import FastMCP\nm = FastMCP('test')\n@m.tool()\n"
        "def echo(text: str) -> str: return text\nm.run()\n"
    )
    payload = {"server": {"command": sys.executable, "args": [str(script)]}}
    host = await client.post("/api/mcp/test", json=payload)
    assert host.json()["ok"] is True, host.text
    assert host.json()["scope"] == "host"
    assert host.json()["runtime_verified"] is False
    runtime = await client.post(f"/api/conversations/{cid}/mcp/test", json=payload)
    assert runtime.json()["ok"] is True, runtime.text
    assert runtime.json()["scope"] == "runtime"
    assert runtime.json()["runtime_verified"] is True
    relative = await client.get(
        f"/api/conversations/{cid}/file/download", params={"path": "mcp_server.py"}
    )
    assert relative.status_code == 422


@pytest.mark.asyncio
async def test_conversation_close_stops_runtime_terminal(runtime_client, tmp_path):
    client, app = runtime_client
    root = tmp_path / "workspace"
    cid = await _create(client, root)
    response = await client.post(
        f"/api/conversations/{cid}/bash/start_bash_command",
        json={"command": "echo $$ > pid; sleep 60"},
    )
    assert response.status_code == 200
    async with asyncio.timeout(5):
        while not (root / "pid").exists():
            await asyncio.sleep(0.01)
    pid = int((root / "pid").read_text())
    service = await app.state.conversation_service.get_event_service(UUID(cid))
    await service.close()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
async def test_scoped_vscode_defaults_to_conversation_workspace(
    runtime_client, tmp_path, monkeypatch
):
    client, _ = runtime_client
    root = tmp_path / "workspace"
    cid = await _create(client, root)
    service = VSCodeService(port=18765)
    monkeypatch.setattr(vscode_service_module, "_vscode_service", service)
    response = await client.get(f"/api/conversations/{cid}/vscode/url")
    assert response.status_code == 200, response.text
    assert response.json()["url"] == service.get_vscode_url(workspace_dir=str(root))


def test_runtime_routes_are_registered_with_dispatch_adapter():
    class CustomRuntimeRoute(APIRoute):
        pass

    router = create_runtime_router(CustomRuntimeRoute)
    assert router.routes
    assert all(isinstance(route, CustomRuntimeRoute) for route in router.routes)


def test_legacy_registration_exposes_deprecation_without_deprecating_local_routes():
    async def endpoint():
        return {"ok": True}

    legacy = APIRouter()
    add_legacy_runtime_routes(legacy, "bash", endpoint, endpoint)
    app = FastAPI()
    app.include_router(legacy, prefix="/api")
    for operations in app.openapi()["paths"].values():
        for operation in operations.values():
            assert operation["deprecated"]
            assert "v1.53.0" in operation["description"]
    paths = create_app(Config()).openapi()["paths"]
    for path, operations in paths.items():
        if "/bash/" in path:
            assert all(not op.get("deprecated", False) for op in operations.values())
