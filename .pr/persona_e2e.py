"""Live check: an agent profile's persona reaches the LLM request.

Starts a real agent-server from this checkout plus Agent Canvas's recording
mock LLM, launches conversations by agent_profile_id, and reads back the system
message the LLM actually received.

    .venv/bin/python .pr/persona_e2e.py \
        --mock <agent-canvas>/tests/e2e/mock-llm/scripts/mock-llm-server.py
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


PERSONA = "You are a ramen chef. Answer only cooking questions."
KEPT = [
    "<MEMORY>",
    "<SECURITY>",
    "<SECURITY_RISK_ASSESSMENT>",
    "<EXTERNAL_SERVICES>",
    "<PROCESS_MANAGEMENT>",
]
REPLACED = [
    "<SOUL>",
    "<ROLE>",
    "<CODE_QUALITY>",
    "<VERSION_CONTROL>",
    "<PULL_REQUESTS>",
]
SUFFIX = "Always cite file paths."
MOCK_PORT = 19777
SERVER_PORT = 19778
SERVER = f"http://127.0.0.1:{SERVER_PORT}"
MOCK = f"http://127.0.0.1:{MOCK_PORT}"


def wait_for(url: str, timeout: float = 90) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit(f"timed out waiting for {url}")


def system_text(request_body: dict) -> list[str]:
    first = request_body["messages"][0]
    assert first["role"] == "system", first["role"]
    content = first["content"]
    if isinstance(content, str):
        return [content]
    return [block["text"] for block in content if block.get("type") == "text"]


def launch(client: httpx.Client, profile_id: str, workdir: Path) -> list[str]:
    httpx.post(f"{MOCK}/admin/reset", json={})
    httpx.post(
        f"{MOCK}/admin/trajectory/register",
        json={"name": "reply", "turns": [{"text": "done"}]},
    ).raise_for_status()
    httpx.post(
        f"{MOCK}/admin/trajectory/activate", json={"name": "reply"}
    ).raise_for_status()
    response = client.post(
        "/api/conversations",
        json={
            "agent_profile_id": profile_id,
            "workspace": {"working_dir": str(workdir)},
            "initial_message": {
                "role": "user",
                "content": [{"type": "text", "text": "What is this repo?"}],
                "run": True,
            },
        },
    )
    response.raise_for_status()
    deadline = time.time() + 60
    while time.time() < deadline:
        requests = httpx.get(f"{MOCK}/admin/requests").json()["requests"]
        agent_steps = [body for body in requests if body.get("tools")]
        if agent_steps:
            return system_text(agent_steps[0])
        time.sleep(0.5)
    raise SystemExit("the LLM never received a request")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", required=True, type=Path)
    args = parser.parse_args()

    root = Path(tempfile.mkdtemp(prefix="persona-e2e-"))
    workdir = root / "workspace"
    workdir.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.endswith("SESSION_API_KEY")}
    env.update(
        HOME=str(root / "home"),
        OH_PERSISTENCE_DIR=str(root / "persist"),
        OH_SECRET_KEY=secrets.token_urlsafe(32),
        OPENHANDS_SUPPRESS_BANNER="1",
        # macOS caps unix socket paths at 104 bytes; tmux's default path can exceed it.
        TMUX_TMPDIR=tempfile.mkdtemp(prefix="oht"),
    )
    python = sys.executable
    logs = (root / "mock.log").open("w"), (root / "server.log").open("w")
    procs = [
        subprocess.Popen(
            [python, str(args.mock), "--port", str(MOCK_PORT)],
            env=env,
            stdout=logs[0],
            stderr=subprocess.STDOUT,
        ),
        subprocess.Popen(
            [
                python,
                "-m",
                "openhands.agent_server",
                "--host",
                "127.0.0.1",
                "--port",
                str(SERVER_PORT),
            ],
            env=env,
            cwd=root,
            stdout=logs[1],
            stderr=subprocess.STDOUT,
        ),
    ]
    try:
        wait_for(f"{MOCK}/admin/requests")
        wait_for(f"{SERVER}/server_info")
        client = httpx.Client(base_url=SERVER, timeout=60)

        capabilities = client.get("/server_info").json()["capabilities"]
        print("capability advertised:", "profile_persona_v1" in capabilities)

        client.post(
            "/api/profiles/mock",
            json={
                "llm": {
                    "model": "openai/mock-model",
                    "base_url": f"{MOCK}/v1",
                    "api_key": "sk-mock",
                    "usage_id": "agent",
                }
            },
        ).raise_for_status()

        base = {"agent_kind": "openhands", "llm_profile_ref": "mock"}
        client.post(
            "/api/agent-profiles/explorer",
            json={**base, "persona": PERSONA, "system_message_suffix": SUFFIX},
        ).raise_for_status()
        client.post("/api/agent-profiles/plain", json=base).raise_for_status()

        rejected = {
            "empty prompt": client.post(
                "/api/agent-profiles/empty", json={**base, "persona": ""}
            ).status_code,
            "ACP profile with prompt": client.post(
                "/api/agent-profiles/acp",
                json={
                    "agent_kind": "acp",
                    "acp_server": "claude-code",
                    "persona": "x",
                },
            ).status_code,
        }
        print("rejected saves:", json.dumps(rejected))

        stored = client.get("/api/agent-profiles/explorer").json()["profile"]
        print("stored persona == PERSONA:", stored["persona"] == PERSONA)
        diag = client.post("/api/agent-profiles/explorer/materialize").json()
        print(
            "materialize resolved_settings.persona == PERSONA:",
            diag["resolved_settings"]["persona"] == PERSONA,
        )

        ids = {
            p["name"]: p["id"]
            for p in client.get("/api/agent-profiles").json()["profiles"]
        }
        for name in ("explorer", "plain"):
            blocks = launch(client, ids[name], workdir)
            joined = "\n\n".join(blocks)
            print(f"\n[{name}] system message blocks: {len(blocks)}")
            print(f"  block 1 starts with persona: {blocks[0].startswith(PERSONA)}")
            print(f"  kept {KEPT}: {[t in blocks[0] for t in KEPT]}")
            print(
                f"  replaced {REPLACED} present: {[t in blocks[0] for t in REPLACED]}"
            )
            print(f"  browser guidance present   : {'<BROWSER_TOOLS>' in blocks[0]}")
            print(f"  suffix present             : {SUFFIX in joined}")
        return 0
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            proc.wait(timeout=20)


if __name__ == "__main__":
    raise SystemExit(main())
