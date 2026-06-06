"""Integration test to verify the agent server works with wsproto."""

import asyncio
import json
import multiprocessing
import os
import socket
import sys
import time
from uuid import uuid4

import pytest
import requests
import websockets
import websockets.exceptions


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.listen(1)
        return s.getsockname()[1]


def run_agent_server(port, api_key):
    # Configure authentication for the server process.
    #
    # Use both the V1 indexed env var and the legacy V0 var to keep this test
    # stable across different config parsing behaviors.
    os.environ["OH_SESSION_API_KEYS_0"] = api_key
    os.environ["SESSION_API_KEY"] = api_key
    sys.argv = ["agent-server", "--port", str(port)]
    from openhands.agent_server.__main__ import main

    main()


@pytest.fixture(scope="session")
def agent_server():
    port = find_free_port()
    api_key = "test-wsproto-key"

    ctx = multiprocessing.get_context("spawn")
    process = ctx.Process(target=run_agent_server, args=(port, api_key))
    process.start()

    for _ in range(30):
        try:
            response = requests.get(f"http://127.0.0.1:{port}/docs", timeout=1)
            if response.status_code == 200:
                break
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(2)
    else:
        process.terminate()
        process.join()
        pytest.fail(f"Agent server failed to start on port {port}")

    yield {"port": port, "api_key": api_key}

    process.terminate()
    process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join()


def test_agent_server_starts_with_wsproto(agent_server):
    response = requests.get(f"http://127.0.0.1:{agent_server['port']}/docs")
    assert response.status_code == 200
    assert (
        "OpenHands Agent Server" in response.text or "swagger" in response.text.lower()
    )


@pytest.mark.asyncio
async def test_agent_server_websocket_with_wsproto(agent_server):
    port = agent_server["port"]
    api_key = agent_server["api_key"]

    response = requests.post(
        f"http://127.0.0.1:{port}/api/conversations",
        headers={"X-Session-API-Key": api_key},
        json={
            "agent": {
                "kind": "Agent",
                "llm": {
                    "usage_id": "test-llm",
                    "model": "test-provider/test-model",
                    "api_key": "test-key",
                },
                "tools": [],
            },
            "workspace": {"working_dir": "/tmp/test-workspace"},
        },
    )
    assert response.status_code in [200, 201]
    conversation_id = response.json()["id"]

    ws_url = (
        f"ws://127.0.0.1:{port}/sockets/events/{conversation_id}"
        f"?session_api_key={api_key}&resend_all=true"
    )

    async with websockets.connect(ws_url, open_timeout=5) as ws:
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=2)
            assert response is not None
        except TimeoutError:
            pass

        await ws.send(
            json.dumps({"role": "user", "content": "Hello from wsproto test"})
        )


@pytest.mark.asyncio
async def test_agent_server_websocket_with_wsproto_header_auth(agent_server):
    port = agent_server["port"]
    api_key = agent_server["api_key"]

    response = requests.post(
        f"http://127.0.0.1:{port}/api/conversations",
        headers={"X-Session-API-Key": api_key},
        json={
            "agent": {
                "kind": "Agent",
                "llm": {
                    "usage_id": "test-llm",
                    "model": "test-provider/test-model",
                    "api_key": "test-key",
                },
                "tools": [],
            },
            "workspace": {"working_dir": "/tmp/test-workspace"},
        },
    )
    assert response.status_code in [200, 201]
    conversation_id = response.json()["id"]

    ws_url = f"ws://127.0.0.1:{port}/sockets/events/{conversation_id}?resend_all=true"

    async with websockets.connect(
        ws_url,
        open_timeout=5,
        additional_headers={"X-Session-API-Key": api_key},
    ) as ws:
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=2)
            assert response is not None
        except TimeoutError:
            pass

        await ws.send(
            json.dumps(
                {"role": "user", "content": "Hello from wsproto header auth test"}
            )
        )


@pytest.mark.asyncio
async def test_agent_server_websocket_first_message_auth_accepted(agent_server):
    """First-message auth: connect with no query/header key, auth via first frame.

    Exercises the real WebSocket protocol transition (handshake → consume first
    frame as auth → continue normal message flow) that mock-only tests can't
    cover. See PR review feedback on test coverage gaps.
    """
    port = agent_server["port"]
    api_key = agent_server["api_key"]

    response = requests.post(
        f"http://127.0.0.1:{port}/api/conversations",
        headers={"X-Session-API-Key": api_key},
        json={
            "agent": {
                "kind": "Agent",
                "llm": {
                    "usage_id": "test-llm",
                    "model": "test-provider/test-model",
                    "api_key": "test-key",
                },
                "tools": [],
            },
            "workspace": {"working_dir": "/tmp/test-workspace"},
        },
    )
    assert response.status_code in [200, 201]
    conversation_id = response.json()["id"]

    # No session_api_key in URL or header — must authenticate via first frame.
    ws_url = f"ws://127.0.0.1:{port}/sockets/events/{conversation_id}?resend_all=true"

    async with websockets.connect(ws_url, open_timeout=5) as ws:
        # Send the auth frame as the very first message after handshake.
        await ws.send(json.dumps({"type": "auth", "session_api_key": api_key}))

        # Connection must remain usable: try to receive (resend_all may produce
        # nothing for an empty conversation, so a timeout here is fine).
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=2)
            assert response is not None
        except TimeoutError:
            pass

        # Subsequent message must be processed as a Message (not auth) — proves
        # the auth frame was consumed by the auth handler, not the main loop.
        await ws.send(
            json.dumps({"role": "user", "content": "Hello after first-message auth"})
        )


@pytest.mark.asyncio
async def test_agent_server_websocket_first_message_auth_rejected(agent_server):
    """First-message auth: invalid key triggers WebSocket close with code 4001."""
    port = agent_server["port"]

    # No conversation needed — auth rejection happens before conversation lookup.
    ws_url = f"ws://127.0.0.1:{port}/sockets/events/{uuid4()}"

    async with websockets.connect(ws_url, open_timeout=5) as ws:
        # Send an invalid first-message auth frame.
        await ws.send(
            json.dumps({"type": "auth", "session_api_key": "definitely-wrong-key"})
        )

        # Server must close the connection with code 4001 ("Authentication
        # failed"). Receiving on a closed socket raises ConnectionClosed.
        with pytest.raises(websockets.exceptions.ConnectionClosed) as exc_info:
            await asyncio.wait_for(ws.recv(), timeout=5)

    assert exc_info.value.rcvd is not None
    assert exc_info.value.rcvd.code == 4001


@pytest.mark.asyncio
async def test_agent_server_websocket_first_message_auth_malformed(agent_server):
    """First-message auth: malformed JSON triggers close with code 4001."""
    port = agent_server["port"]

    ws_url = f"ws://127.0.0.1:{port}/sockets/events/{uuid4()}"

    async with websockets.connect(ws_url, open_timeout=5) as ws:
        # Send invalid JSON as the first frame.
        await ws.send("this is not json")

        with pytest.raises(websockets.exceptions.ConnectionClosed) as exc_info:
            await asyncio.wait_for(ws.recv(), timeout=5)

    assert exc_info.value.rcvd is not None
    assert exc_info.value.rcvd.code == 4001
