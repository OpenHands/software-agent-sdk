import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from openhands.agent_server import mint_conversation_token


KEYS = ["live-key-a", "live-key-b"]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def ws_close_code(url: str, credential: str) -> int | None:
    try:
        with connect(url, open_timeout=10) as websocket:
            websocket.send(json.dumps({"type": "auth", "session_api_key": credential}))
            websocket.recv(timeout=3)
    except ConnectionClosed as exc:
        return exc.rcvd.code if exc.rcvd else None
    except TimeoutError:
        return None
    return None


def main() -> int:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    workdir = tempfile.mkdtemp(prefix="oh-token-probe-")
    env = {
        **os.environ,
        "OH_SESSION_API_KEYS_0": KEYS[0],
        "OH_SESSION_API_KEYS_1": KEYS[1],
        "OH_CONVERSATIONS_PATH": os.path.join(workdir, "conversations"),
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "openhands.agent_server", "--host", "127.0.0.1",
         "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    results: list[tuple[str, object, object]] = []
    try:
        session = {"X-Session-API-Key": KEYS[0]}
        with httpx.Client(base_url=base, timeout=30) as client:
            for _ in range(120):
                try:
                    if client.get("/alive").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.5)
            payload = {
                "agent": {"llm": {"model": "gpt-4o-mini", "api_key": "test"},
                          "tools": []},
                "workspace": {"working_dir": workdir},
                "autotitle": False,
            }
            conv_a = client.post("/api/conversations", json=payload, headers=session)
            conv_b = client.post("/api/conversations", json=payload, headers=session)
            a, b = conv_a.json()["id"], conv_b.json()["id"]
            token = mint_conversation_token(KEYS[1], a, ttl=300)
            bearer = {"X-Session-API-Key": token}

            def check(name: str, method: str, path: str, expected: int) -> None:
                status = client.request(method, path, headers=bearer).status_code
                results.append((name, expected, status))

            def same_as_session_key(name: str, path: str) -> None:
                expected = client.get(path, headers=session).status_code
                results.append((name, expected, client.get(path, headers=bearer).status_code))

            check("own conversation", "GET", f"/api/conversations/{a}", 200)
            check("own events search", "GET", f"/api/conversations/{a}/events/search", 200)
            same_as_session_key("own git changes", f"/api/conversations/{a}/git/changes")
            same_as_session_key("own workspace listing", f"/api/conversations/{a}/workspace")
            same_as_session_key("own file download",
                                f"/api/conversations/{a}/file/download?path=missing.txt")
            check("other conversation", "GET", f"/api/conversations/{b}", 403)
            check("server settings", "GET", "/api/settings", 403)
            check("conversation list", "GET", "/api/conversations", 403)
            check("conversation search", "GET", "/api/conversations/search", 403)
            check("server bash", "POST", "/api/bash/execute_bash_command", 403)
            check("own direct shell", "POST",
                  f"/api/conversations/{a}/bash/execute_bash_command", 403)
            check("own vscode url", "GET", f"/api/conversations/{a}/vscode/url", 403)
            check("other trajectory via own runtime", "GET",
                  f"/api/conversations/{a}/file/download-trajectory/{b}", 403)
            check("mint workspace cookie", "POST", "/api/auth/workspace-session", 403)
            retired = mint_conversation_token("retired-key", a, ttl=300)
            results.append(("unknown signing key", 401, client.get(
                f"/api/conversations/{a}",
                headers={"X-Session-API-Key": retired}).status_code))
            results.append(("session key unchanged", 200, client.get(
                "/api/settings", headers=session).status_code))

        ws = f"ws://127.0.0.1:{port}"
        results.append(("socket own conversation", "open",
                        ws_close_code(f"{ws}/sockets/session/{a}", token) or "open"))
        results.append(("socket other conversation", 4003,
                        ws_close_code(f"{ws}/sockets/session/{b}", token)))
        results.append(("server-wide bash socket", 4001,
                        ws_close_code(f"{ws}/sockets/bash-events", token)))
    finally:
        server.terminate()
        server.wait(timeout=30)

    failed = 0
    for name, expected, actual in results:
        ok = expected == actual
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:34} expected={expected} got={actual}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
