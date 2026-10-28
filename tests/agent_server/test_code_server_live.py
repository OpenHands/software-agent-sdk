"""Opt-in tests against the verified release: CODE_SERVER_ROOT=/path/to/release."""

import asyncio
import os
import socket
from pathlib import Path

import httpx
import pytest
import websockets

from openhands.agent_server.vscode_service import VSCodeService


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", [None, "/runtime/editor"])
async def test_real_editor_auth_paths_websocket_and_rotation(
    tmp_path, monkeypatch, prefix
):
    root = os.environ.get("CODE_SERVER_ROOT")
    if not root or not (Path(root) / "bin/code-server").is_file():
        pytest.skip("Set CODE_SERVER_ROOT to an extracted code-server release")
    monkeypatch.setenv("HOME", str(tmp_path))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    service = VSCodeService(
        port=port, connection_token="first-test-token", server_base_path=prefix
    )
    base = f"http://127.0.0.1:{port}"
    path = (prefix or "") + "/"
    try:
        assert await service.start()
        async with httpx.AsyncClient(base_url=base) as client:
            assert (await client.get(path)).status_code == 401
            assert (await client.get(path, params={"tkn": "wrong"})).status_code == 401
            if prefix:
                assert (await client.get("/")).status_code == 404
            secure = await client.get(
                path,
                params={"tkn": "first-test-token"},
                headers={"X-Forwarded-Proto": "https"},
            )
            assert "Secure" in secure.headers["set-cookie"]
            assert "SameSite=None" in secure.headers["set-cookie"]
            client.cookies.clear()
            folder = str(tmp_path / "space & hash# question?")
            response = await client.get(service.get_vscode_url(base, folder))
            assert response.status_code == 303
            assert "tkn=" not in response.headers["location"]
            assert "HttpOnly" in response.headers["set-cookie"]
            assert "no-store" in response.headers["cache-control"]
            response = await client.get(response.headers["location"])
            assert response.status_code == 200
            assert "vscode-workbench-web-configuration" in response.text
            assert (await client.get(path + "healthz")).status_code == 200
            assert (
                await client.get(path, headers={"Origin": "https://evil.invalid"})
            ).status_code == 403
            cookies = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
            ws_url = base.replace("http", "ws") + path + "healthz"
            async with websockets.connect(
                ws_url, additional_headers={"Cookie": cookies}, origin=base
            ) as ws:
                await ws.send("ping")
                assert await asyncio.wait_for(ws.recv(), 5)
            with pytest.raises(websockets.exceptions.InvalidStatus):
                async with websockets.connect(ws_url, origin=base):
                    pass
            with pytest.raises(websockets.exceptions.InvalidStatus):
                async with websockets.connect(
                    ws_url,
                    additional_headers={"Cookie": cookies},
                    origin="https://evil.invalid",
                ):
                    pass
            await service.set_connection_token("second-test-token")
            assert service.is_running()
            assert (await client.get(path)).status_code == 401
            assert (
                await client.get(path, params={"tkn": "first-test-token"})
            ).status_code == 401
            assert (
                await client.get(service.get_vscode_url(base, folder))
            ).status_code == 303
    finally:
        await service.stop()
    assert not service.is_running()
    assert await service._is_port_available()
