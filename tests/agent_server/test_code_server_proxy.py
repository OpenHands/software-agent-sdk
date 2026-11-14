"""Proxy treats only a non-empty tkn as the VS Code session handshake."""

import json
import os
import shutil
import signal
import socket
import subprocess
import threading
from pathlib import Path

import httpx
import pytest


PROXY = (
    Path(__file__).resolve().parents[2]
    / "openhands-agent-server"
    / "openhands"
    / "agent_server"
    / "code_server_proxy.cjs"
)
TOKEN = "editor-token"
RESOURCE = "/stable-test/vscode-remote-resource"
THEME_PATH = (
    "/openhands/.code-server/lib/vscode/extensions/theme-defaults/themes/dark_plus.json"
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _write_stub(root: Path) -> None:
    binary = root / "bin" / "code-server"
    binary.parent.mkdir(parents=True)
    binary.write_text(
        "\n".join(
            (
                "#!/usr/bin/env node",
                'const http = require("node:http");',
                "const args = process.argv.slice(2);",
                'const socketPath = args[args.indexOf("--socket") + 1];',
                "const server = http.createServer((req, res) => {",
                '  res.writeHead(200, {"content-type": "text/plain"});',
                '  res.end("upstream-ok " + req.url);',
                "});",
                "server.listen(socketPath);",
                "",
            )
        )
    )
    binary.chmod(0o755)


def _stop(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
@pytest.mark.parametrize("base_path", [None, "vscode"])
def test_empty_tkn_resource_uses_editor_cookie(tmp_path: Path, base_path: str | None):
    """An empty tkn on vscode-remote-resource is not a failed handshake.

    After the real token exchange, ``&tkn=`` plus the editor cookie is proxied.
    The same URL without a cookie is 401, and a non-empty wrong tkn is 401
    even when the cookie is present.
    """
    root = tmp_path / "code-server"
    _write_stub(root)
    port = _free_port()
    prefix = f"/{base_path.strip('/')}" if base_path else ""
    proc = subprocess.Popen(
        ["node", str(PROXY)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    output: list[str] = []
    ready = threading.Event()

    def _drain() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            output.append(line)
            if "OpenHands editor ready" in line:
                ready.set()

    threading.Thread(target=_drain, daemon=True).start()
    assert proc.stdin is not None
    proc.stdin.write(
        json.dumps(
            {
                "root": str(root),
                "port": port,
                "token": TOKEN,
                "basePath": base_path,
            }
        )
    )
    proc.stdin.close()
    try:
        if not ready.wait(10):
            raise AssertionError(
                f"proxy did not become ready (exit {proc.poll()}): " + "".join(output)
            )
        base = f"http://127.0.0.1:{port}"
        resource = prefix + RESOURCE
        empty = {"path": THEME_PATH, "tkn": ""}
        with httpx.Client(base_url=base) as client:
            denied = client.get(resource, params=empty)
            assert denied.status_code == 401

            wrong = client.get(resource, params={**empty, "tkn": "wrong"})
            assert wrong.status_code == 401

            exchange = client.get(f"{prefix}/", params={"tkn": TOKEN})
            assert exchange.status_code == 303
            assert "tkn=" not in exchange.headers["location"]
            assert "HttpOnly" in exchange.headers["set-cookie"]

            authed = client.get(resource, params=empty)
            assert authed.status_code == 200
            assert authed.text.startswith("upstream-ok ")
            assert "tkn=" in authed.text
            assert "vscode-remote-resource" in authed.text

            mismatched = client.get(resource, params={**empty, "tkn": "wrong"})
            assert mismatched.status_code == 401

            client.cookies.clear()
            assert client.get(resource, params=empty).status_code == 401
    finally:
        _stop(proc)
