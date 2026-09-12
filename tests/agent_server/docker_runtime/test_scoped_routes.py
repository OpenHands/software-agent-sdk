import asyncio
import socket
from uuid import UUID

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from openhands.agent_server.api import create_app
from openhands.agent_server.bash_router import bash_router
from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.routers import docker_global_proxy_router
from openhands.agent_server.runtime_router import create_runtime_router
from tests.agent_server.docker_runtime.test_docker_routers import _StubRegistry
from tests.agent_server.test_runtime_router import _create, runtime_client


__all__ = ["runtime_client"]


@pytest.mark.asyncio
async def test_docker_scoped_routes_reach_real_inner_runtime(runtime_client, tmp_path):
    client, inner = runtime_client
    cid = await _create(client, tmp_path / "workspace")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(inner, lifespan="off", log_level="error"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
        outer = create_app(
            Config(
                conversation_runtime="docker",
                session_api_keys=["runtime-route-test-key"],
            )
        )
        registry = _StubRegistry(
            port, "runtime-route-test-key", tmp_path / "conversations"
        )
        registry.preregister(UUID(cid))
        outer.state.docker_registry = registry
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=outer),
            base_url="http://outer",
            headers={"X-Session-API-Key": "runtime-route-test-key"},
        ) as proxy:
            result = await proxy.post(
                f"/api/conversations/{cid}/bash/execute_bash_command",
                json={"command": "printf proxied > marker"},
            )
            assert result.status_code == 200, result.text
            download = await proxy.get(
                f"/api/conversations/{cid}/file/download",
                params={"path": str(tmp_path / "workspace" / "marker")},
            )
            assert download.text == "proxied"
            probe = await proxy.post(
                f"/api/conversations/{cid}/mcp/test",
                json={"server": {"command": "/does/not/exist"}, "timeout": 1},
            )
            assert probe.status_code == 200, probe.text
            assert probe.json()["scope"] == "runtime"
            assert probe.json()["ok"] is False
            assert probe.json()["runtime_verified"] is False
            bad_path = await proxy.get(f"/api/conversations/{cid}/file/download")
            assert bad_path.status_code == 422
            unauthorized = await proxy.get(
                f"/api/conversations/{cid}/git/changes",
                headers={"X-Session-API-Key": "wrong"},
            )
            assert unauthorized.status_code == 401
    finally:
        server.should_exit = True
        await task
        sock.close()


def test_only_legacy_docker_routes_are_deprecated():
    app = FastAPI()
    app.include_router(docker_global_proxy_router, prefix="/api")
    paths = app.openapi()["paths"]
    assert len(paths) == 10
    for operations in paths.values():
        for operation in operations.values():
            assert operation["deprecated"] is True
            assert (
                "Deprecated since v1.48.0 and scheduled for removal in v1.53.0."
                in operation["description"]
            )
            assert "/api/conversations/{id}/" in operation["description"]
    local = FastAPI()
    local.include_router(bash_router, prefix="/api")
    local.include_router(create_runtime_router(), prefix="/api")
    for path, operations in local.openapi()["paths"].items():
        for operation in operations.values():
            assert "v1.53.0" not in operation.get("description", "")
            if "/bash/" in path:
                assert not operation.get("deprecated", False)
