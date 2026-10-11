"""Exercise failed-run logging through the real CLI, HTTP and WebSocket stack.

The LLM target is a reserved, non-listening loopback port: the real transport
fails without contacting a provider. A fresh process is necessary because
pytest's handlers and imported logging settings hide the default Rich path.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from websockets.asyncio.client import connect


@pytest.fixture(params=["rich", "plain", "json"])
def run_error_server(request, tmp_path: Path) -> Generator[tuple[str, Path]]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "session_api_keys": [],
                "conversations_path": str(tmp_path / "conversations"),
                "workspace_path": str(workspace),
                "bash_events_dir": str(tmp_path / "bash-events"),
                "telemetry": {"exporter": "none"},
            }
        )
    )
    # Do not inherit provider credentials, CI logging, proxies or user settings.
    inherited = ("PATH", "USER", "TMPDIR", "LANG", "LC_ALL", "TZ")
    env = {key: os.environ[key] for key in inherited if key in os.environ}
    env.update(
        HOME=str(home),
        OPENHANDS_AGENT_SERVER_CONFIG_PATH=str(config),
        OH_PERSISTENCE_DIR=str(tmp_path / "persistence"),
        PYTHONUNBUFFERED="1",
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        LOG_TO_FILE="true",
        LOG_DIR=str(tmp_path / "logs"),
    )
    if request.param == "plain":
        env["LOG_RICH_TRACEBACKS"] = "false"
    elif request.param == "json":
        env["LOG_JSON"] = "true"
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    host = f"http://127.0.0.1:{port}"
    console_log = tmp_path / "console.log"
    with console_log.open("wb") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "openhands.agent_server",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=tmp_path,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 60
            with httpx.Client(base_url=host, trust_env=False, timeout=0.5) as client:
                while time.monotonic() < deadline:
                    assert process.poll() is None, console_log.read_text()
                    try:
                        if client.get("/alive").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail(f"CLI startup timed out: {console_log.read_text()}")
                # Exclude one-time tool/version discovery from the failure probe.
                client.get("/server_info", timeout=10).raise_for_status()
            yield host, tmp_path
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                    pytest.fail("Agent server did not stop within 10 seconds")


@pytest.mark.asyncio
async def test_failed_run_logging_keeps_live_server_responsive(run_error_server):
    host, directory = run_error_server
    samples: dict[str, list[tuple[float, float, int]]] = {
        "/alive": [],
        "/server_info": [],
    }
    stop = asyncio.Event()

    async def probe(client: httpx.AsyncClient, endpoint: str) -> None:
        while not stop.is_set():
            started, clock = time.time(), time.monotonic()
            response = await client.get(endpoint)
            samples[endpoint].append(
                (started, time.monotonic() - clock, response.status_code)
            )
            await asyncio.sleep(0.02)

    # Keep this port reserved but never listen, so no other service can receive
    # the dummy LLM request while the real connection-error chain is produced.
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        llm_port = unavailable.getsockname()[1]
        async with httpx.AsyncClient(
            base_url=host, trust_env=False, timeout=8
        ) as client:
            response = await client.post(
                "/api/conversations",
                json={
                    "agent": {
                        "kind": "Agent",
                        "llm": {
                            "model": "openai/offline",
                            "api_key": "dummy",
                            "base_url": f"http://127.0.0.1:{llm_port}/v1",
                            "num_retries": 0,
                        },
                        "tools": [],
                    },
                    "workspace": {"working_dir": str(directory / "workspace")},
                    "autotitle": False,
                },
            )
            response.raise_for_status()
            conversation = f"/api/conversations/{response.json()['id']}"
            response = await client.post(
                f"{conversation}/events",
                json={"content": [{"type": "text", "text": "hi"}], "run": False},
            )
            response.raise_for_status()
            socket_url = host.replace("http://", "ws://")
            socket_url += f"/sockets/events/{conversation.rsplit('/', 1)[1]}"
            async with connect(
                socket_url, proxy=None, open_timeout=8, close_timeout=1
            ) as websocket:
                initial = json.loads(await asyncio.wait_for(websocket.recv(), 8))
                assert initial["kind"] == "ConversationStateUpdateEvent"

                async def receive_failure():
                    error_event = None
                    error_received = None
                    terminal_error = False
                    while error_event is None or not terminal_error:
                        event = json.loads(await websocket.recv())
                        if event["kind"] == "ConversationErrorEvent":
                            error_event, error_received = event, time.time()
                        if event["kind"] == "ConversationStateUpdateEvent":
                            value = event["value"]
                            state = (
                                value.get("execution_status")
                                if event["key"] == "full_state"
                                else value
                                if event["key"] == "execution_status"
                                else None
                            )
                            terminal_error |= state == "error"
                    return error_event, error_received

                tasks = [
                    asyncio.create_task(probe(client, endpoint)) for endpoint in samples
                ]
                receiver = asyncio.create_task(receive_failure())
                try:
                    await asyncio.sleep(0.1)
                    async with asyncio.timeout(20):
                        response = await client.post(f"{conversation}/run")
                        response.raise_for_status()
                        error_event, error_received = await receiver
                    await asyncio.sleep(0.2)
                finally:
                    stop.set()
                    receiver.cancel()
                    await asyncio.gather(receiver, return_exceptions=True)
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    assert not any(isinstance(r, BaseException) for r in results), (
                        results
                    )

    assert error_event is not None and error_received is not None
    assert "Connection error" in error_event["detail"]
    emitted = datetime.fromisoformat(error_event["timestamp"]).timestamp()
    assert error_received - emitted < 1.5, "Run-error logging delayed the error frame"
    for endpoint, measurements in samples.items():
        assert any(started < emitted for started, _, _ in measurements), endpoint
        assert any(started > error_received for started, _, _ in measurements), endpoint
        assert all(status == 200 for _, _, status in measurements), measurements
        slowest = max(elapsed for _, elapsed, _ in measurements)
        assert slowest < 1.5, (
            f"{endpoint} stalled for {slowest:.3f}s during run failure"
        )
    diagnostic = (directory / "logs" / "app.log").read_text()
    assert "Error during conversation run" in diagnostic
    assert "ConversationRunError" in diagnostic
    assert "Connection error" in diagnostic
