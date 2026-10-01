"""Live check: the OpenAI gateway answers through an ACP agent profile.

Starts an agent-server from an SDK checkout whose global settings and active
Agent Profile both run the canvas mock ACP server, then calls
``/v1/chat/completions`` with a model naming a saved LLM profile.

    python .pr/gateway_acp_e2e.py --sdk <sdk checkout> --mock-acp <mock-acp-server.py>
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


TOKEN = "GATEWAY_ACP_E2E_OK"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--mock-acp", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18495)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    sdk = args.sdk.resolve()
    home = Path(tempfile.mkdtemp(prefix="gateway-acp-e2e-"))
    (home / "workspace").mkdir()
    tmux = Path(f"/tmp/oha{args.port}")
    tmux.mkdir(exist_ok=True)
    python = str(sdk / ".venv" / "bin" / "python")
    env = {
        **os.environ,
        "OH_PERSISTENCE_DIR": str(home / "persistence"),
        "OH_CONVERSATIONS_PATH": str(home / "conversations"),
        "OH_WORKSPACE_PATH": str(home / "workspace"),
        "OH_SECRET_KEY": "gateway-acp-e2e-key",
        "OPENHANDS_SUPPRESS_BANNER": "1",
        "TMUX_TMPDIR": str(tmux),
        "OH_ENABLE_VSCODE": "false",
    }
    server = subprocess.Popen(
        [python, "-m", "openhands.agent_server", "--port", str(args.port)],
        env=env,
        cwd=home,
        stdout=(home / "server.log").open("w"),
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{args.port}"
    acp_command = [python, str(args.mock_acp.resolve()), "--reply-token", TOKEN]
    rows: dict[str, object] = {"state": str(home)}
    gateway: dict[str, object] = {}
    unknown_status = 0
    try:
        for _ in range(240):
            if server.poll() is not None:
                sys.exit(f"agent-server exited early; see {home}")
            try:
                if httpx.get(f"{base}/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        client = httpx.Client(base_url=base, timeout=180)
        client.post(
            "/api/profiles/mock",
            json={"llm": {"model": "openai/mock-model", "api_key": "sk"}},
        ).raise_for_status()
        acp = {"agent_kind": "acp", "acp_server": "custom"}
        settings = client.patch(
            "/api/settings",
            json={"agent_settings_diff": {**acp, "acp_command": acp_command}},
        )
        rows["global settings set to ACP"] = settings.status_code
        saved = client.post(
            "/api/agent-profiles/acp-mock",
            json={
                **acp,
                "acp_command": shlex.join(acp_command),
                "mcp_server_refs": [],
            },
        )
        rows["ACP agent profile saved"] = saved.status_code
        profiles = client.get("/api/agent-profiles").json()["profiles"]
        profile_id = next(p["id"] for p in profiles if p["name"] == "acp-mock")
        rows["ACP agent profile activated"] = client.post(
            f"/api/agent-profiles/{profile_id}/activate"
        ).status_code

        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "openhands_mock",
                "messages": [
                    {"role": "system", "content": "Answer briefly."},
                    {"role": "user", "content": "hello"},
                ],
            },
        )
        content = ""
        if response.status_code == 200:
            content = response.json()["choices"][0]["message"]["content"] or ""
        gateway = {
            "status": response.status_code,
            "content": content,
            "detail": None if response.status_code == 200 else response.text[:300],
        }
        rows["gateway"] = gateway
        unknown_status = client.post(
            "/v1/chat/completions",
            json={
                "model": "openhands_missing",
                "messages": [{"role": "user", "content": "hello"}],
            },
        ).status_code
        rows["gateway, unknown model"] = unknown_status
    finally:
        server.send_signal(signal.SIGINT)
        try:
            server.wait(timeout=60)
        except subprocess.TimeoutExpired:
            server.kill()

    print(json.dumps(rows, indent=2))
    verdicts = {
        "gateway answers through the ACP agent": gateway.get("status") == 200
        and TOKEN in str(gateway.get("content")),
        "an unknown model is a 404": unknown_status == 404,
    }
    print("\n# Verdicts")
    for name, ok in verdicts.items():
        print(f"- {'PASS' if ok else 'FAIL'}  {name}")
    if args.out:
        args.out.write_text(json.dumps({"rows": rows, "verdicts": verdicts}, indent=2))
    return 0 if all(verdicts.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
