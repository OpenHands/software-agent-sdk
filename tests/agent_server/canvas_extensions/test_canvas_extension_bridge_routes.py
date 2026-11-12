"""Route binding of the Canvas App backend bridge: which upstream path is proxied."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from openhands.agent_server import canvas_extensions_bridge_router as bridge_router


APP_NAME = "demo-app"


@pytest.fixture
def proxied(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, list[str]]:
    paths: list[str] = []

    async def fake_http(request: Request, extension_name: str, path: str) -> Response:
        paths.append(path)
        return Response(status_code=204)

    async def fake_websocket(
        websocket: WebSocket, extension_name: str, path: str
    ) -> None:
        paths.append(path)
        await websocket.close(code=1011)

    monkeypatch.setattr(bridge_router, "proxy_app_backend_http", fake_http)
    monkeypatch.setattr(bridge_router, "proxy_app_backend_websocket", fake_websocket)
    app = FastAPI()
    app.include_router(bridge_router.app_backend_bridge_router)
    return TestClient(app), paths


@pytest.mark.parametrize(
    ("url", "expected_path"),
    [
        (f"/app-backends/{APP_NAME}?path=echo", ""),
        (f"/app-backends/{APP_NAME}?path=..", ""),
        (f"/app-backends/{APP_NAME}/?path=echo", ""),
        (f"/app-backends/{APP_NAME}/echo?path=other", "echo"),
    ],
)
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_http_path_comes_from_url_path_not_query(
    proxied: tuple[TestClient, list[str]], method: str, url: str, expected_path: str
) -> None:
    client, paths = proxied

    assert client.request(method, url).status_code == 204
    assert paths == [expected_path]


@pytest.mark.parametrize(
    ("url", "expected_path"),
    [
        (f"/app-backends/{APP_NAME}?path=ws", ""),
        (f"/app-backends/{APP_NAME}/ws?path=other", "ws"),
    ],
)
def test_websocket_path_comes_from_url_path_not_query(
    proxied: tuple[TestClient, list[str]], url: str, expected_path: str
) -> None:
    client, paths = proxied

    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(url):
            pass
    assert closed.value.code == 1011
    assert paths == [expected_path]
