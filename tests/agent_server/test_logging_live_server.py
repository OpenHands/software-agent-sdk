"""Stock CLI server with a synthetic service failure, no provider or model calls.

The cyclic shape is injected, not a reproduction of the reporter's deployment.
A child process and external timeouts keep the pre-fix wedge safe to test.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil
import pytest
from pydantic import SecretStr
from websockets.sync.client import connect

from openhands.sdk import LLM, Agent
from openhands.workspace.docker.workspace import find_available_tcp_port


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX SIGTERM shutdown contract")
@pytest.mark.parametrize("probe", ["socket", "handler"])
def test_cyclic_failure_keeps_server_responsive(tmp_path: Path, probe: str):
    port = find_available_tcp_port()
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "session_api_keys": [],
                "conversations_path": str(tmp_path / "conversations"),
                "workspace_path": str(tmp_path / "workspace"),
                "bash_events_dir": str(tmp_path / "bash"),
            }
        )
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("OH_") and key != "SESSION_API_KEY"
    }
    env.update(
        {
            "OPENHANDS_AGENT_SERVER_CONFIG_PATH": str(config),
            "OH_PERSISTENCE_DIR": str(tmp_path / "persist"),
            "LOG_AUTO_CONFIG": "true",
            "LOG_RICH_TRACEBACKS": "true",
            "LOG_JSON": "false",
            "LOG_TO_FILE": "false",
            "DEBUG_LLM": "false",
            "CI": "false",
            "GITHUB_ACTIONS": "",
            "LITELLM_LOCAL_MODEL_COST_MAP": "true",
        }
    )
    log_path = tmp_path / "server.log"
    with (
        log_path.open("w") as log,
        httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=3, trust_env=False
        ) as client,
    ):
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "openhands.agent_server",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--import-modules",
                "tests.agent_server.logging_failure_probe",
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                assert process.poll() is None, log_path.read_text()
                try:
                    if client.get("/alive").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail(f"Server did not start: {log_path.read_text()}")
            agent = Agent(
                llm=LLM(model="gpt-4o-mini", api_key=SecretStr("test")), tools=[]
            )
            response = client.post(
                "/api/conversations",
                json={
                    "agent": agent.model_dump(
                        mode="json", context={"expose_secrets": True}
                    ),
                    "workspace": {"working_dir": str(tmp_path / "workspace")},
                },
                timeout=15,
            )
            assert response.status_code == 201, response.text
            conversation_id = response.json()["id"]
            rss_before = psutil.Process(process.pid).memory_info().rss
            with connect(
                f"ws://127.0.0.1:{port}/sockets/events/{conversation_id}",
                open_timeout=3,
                close_timeout=1,
                proxy=None,
            ) as socket:
                for _ in range(5):
                    socket.send(
                        json.dumps(
                            {
                                "role": "user",
                                "content": [{"type": "text", "text": probe}],
                            }
                        )
                    )
                    deadline = time.monotonic() + 3
                    while True:
                        event = json.loads(
                            socket.recv(timeout=max(0, deadline - time.monotonic()))
                        )
                        if event.get("code") == "RuntimeError":
                            assert event["detail"] == "injected cyclic exception"
                            break
                    for path in (
                        "/alive",
                        "/server_info",
                        f"/api/conversations/{conversation_id}",
                    ):
                        assert client.get(path).status_code == 200, path
            rss_growth = psutil.Process(process.pid).memory_info().rss - rss_before
            assert rss_growth < 64 * 1024 * 1024, (
                f"Logging RSS growth: {rss_growth} bytes"
            )
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=5)
            assert process.returncode in (0, -signal.SIGTERM)
            print(
                f"{probe}: 5 error replies, 15 HTTP probes OK, "
                f"RSS delta={rss_growth}; SIGTERM={process.returncode}"
            )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                    print(
                        "SIGTERM timed out; watchdog killed child: "
                        f"{process.returncode}"
                    )
