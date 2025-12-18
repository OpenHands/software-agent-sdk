"""Integration test to verify the agent server works with wsproto."""

import asyncio
import json
import multiprocessing
import os
import socket
import sys
import time

import pytest
import requests
import websockets


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.listen(1)
        return s.getsockname()[1]


def run_agent_server(port, api_key):
    os.environ["OH_SESSION_API_KEYS"] = f'["{api_key}"]'
    sys.argv = ["agent-server", "--port", str(port)]
    from openhands.agent_server.__main__ import main

    main()


@pytest.fixture(scope="session")
def agent_server():
    port = find_free_port()
    api_key = "test-wsproto-key"

    process = multiprocessing.Process(target=run_agent_server, args=(port, api_key))
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
