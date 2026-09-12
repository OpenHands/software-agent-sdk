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
