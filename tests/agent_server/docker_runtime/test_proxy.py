import asyncio

import pytest
from starlette.websockets import WebSocket

from openhands.agent_server.docker_runtime.proxy import _bridge_websocket_loop


class Upstream:
    def __init__(self, fail=False):
        self.fail = fail
        self.closed = False
        self.stopped = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            if self.fail:
                raise RuntimeError("invalid upstream frame")
            await asyncio.Future()
        finally:
            self.stopped.set()

    async def close(self):
        self.closed = True


async def client_socket():
    incoming = asyncio.Queue()
    await incoming.put({"type": "websocket.connect"})
    sent = []

    async def send(message):
        sent.append(message)

    client = WebSocket({"type": "websocket"}, incoming.get, send)
    await client.accept()
    return client, sent


@pytest.mark.asyncio
async def test_bridge_propagates_upstream_error_and_closes_connections():
    client, sent = await client_socket()
    upstream = Upstream(fail=True)
    with pytest.raises(RuntimeError, match="invalid upstream frame"):
        await _bridge_websocket_loop(client, upstream)
    assert upstream.closed
    assert sent[-1]["type"] == "websocket.close"


@pytest.mark.asyncio
async def test_bridge_cancellation_closes_connections_and_child_tasks():
    client, sent = await client_socket()
    upstream = Upstream()
    task = asyncio.create_task(_bridge_websocket_loop(client, upstream))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert upstream.closed
    assert upstream.stopped.is_set()
    assert sent[-1]["type"] == "websocket.close"


def request_and_target():
    from starlette.requests import Request

    from openhands.agent_server.docker_runtime.registry import (
        RunningConversationContainer,
    )

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return (
        Request({"type": "http", "method": "GET", "headers": []}, receive),
        RunningConversationContainer("http://upstream", "inner-key", None, "test"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["ReadTimeout", "WriteError", "RemoteProtocolError", "cancel"]
)
async def test_http_setup_failure_closes_client_and_preserves_cancellation(
    monkeypatch, failure
):
    import httpx
    from fastapi import HTTPException

    from openhands.agent_server.docker_runtime import proxy

    errors = {
        "ReadTimeout": httpx.ReadTimeout,
        "WriteError": httpx.WriteError,
        "RemoteProtocolError": httpx.RemoteProtocolError,
        "cancel": asyncio.CancelledError,
    }

    def respond(request):
        raise errors[failure]("upstream failed")

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(proxy.httpx, "AsyncClient", lambda **kwargs: client)
    expected = asyncio.CancelledError if failure == "cancel" else HTTPException
    with pytest.raises(expected) as raised:
        await proxy.proxy_http(*request_and_target(), upstream_path="/api/test")
    if isinstance(raised.value, HTTPException):
        assert raised.value.status_code == 502
    assert client.is_closed


@pytest.mark.asyncio
async def test_http_client_lives_until_the_stream_is_consumed(monkeypatch):
    import httpx

    from openhands.agent_server.docker_runtime import proxy

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"result"

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=Body())
        )
    )
    monkeypatch.setattr(proxy.httpx, "AsyncClient", lambda **kwargs: client)
    response = await proxy.proxy_http(*request_and_target(), upstream_path="/api/test")
    assert not client.is_closed
    assert [chunk async for chunk in response.body_iterator] == [b"result"]
    assert client.is_closed
