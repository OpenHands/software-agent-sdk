"""Live check: ACP conversations never call a provider for titles (#5432).

Starts an agent-server from an SDK checkout with global ACP settings (the
canvas mock ACP server), an activated LLM profile, and a fake OpenAI endpoint
in ``OPENAI_BASE_URL`` that records every request. Then:

1. a canvas-style launch (ACP ``agent_settings`` without ``llm``),
2. a launch that round-trips ``GET /api/settings`` into ``agent_settings``,
3. a launch with ``title_llm_profile`` set,
4. an SDK ``LocalConversation.generate_title()`` on a settings-built agent.

    python .pr/acp_title_e2e.py --sdk <sdk checkout> --mock-acp <mock-acp-server.py>
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import httpx


PROFILE_KEY = "sk-profile-secret"
ENV_KEY = "sk-server-env-key"
PROFILE_TITLE = "Mock Profile Title"
MESSAGE = "Fix the login bug"


class FakeOpenAI(BaseHTTPRequestHandler):
    seen: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOpenAI.seen.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "model": body.get("model"),
            }
        )
        reply = {
            "id": "chatcmpl-e2e",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model"),
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": PROFILE_TITLE},
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass


def take_requests() -> list[dict[str, Any]]:
    seen = list(FakeOpenAI.seen)
    FakeOpenAI.seen.clear()
    return seen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--mock-acp", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18531)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    sdk = args.sdk.resolve()
    python = str(sdk / ".venv" / "bin" / "python")
    home = Path(tempfile.mkdtemp(prefix="acp-title-e2e-"))
    (home / "workspace").mkdir()
    tmux = Path(f"/tmp/oht{args.port}")
    tmux.mkdir(exist_ok=True)

    fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAI)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    fake_url = f"http://127.0.0.1:{fake.server_address[1]}/v1"

    env = {
        **os.environ,
        "OH_PERSISTENCE_DIR": str(home / "persistence"),
        "OH_CONVERSATIONS_PATH": str(home / "conversations"),
        "OH_WORKSPACE_PATH": str(home / "workspace"),
        "OH_SECRET_KEY": "acp-title-e2e-key",
        "OPENHANDS_SUPPRESS_BANNER": "1",
        "TMUX_TMPDIR": str(tmux),
        "OH_ENABLE_VSCODE": "false",
        "OPENAI_BASE_URL": fake_url,
        "OPENAI_API_KEY": ENV_KEY,
    }
    server = subprocess.Popen(
        [python, "-m", "openhands.agent_server", "--port", str(args.port)],
        env=env,
        cwd=home,
        stdout=(home / "server.log").open("w"),
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{args.port}"
    acp = {
        "agent_kind": "acp",
        "acp_server": "custom",
        "acp_command": [python, str(args.mock_acp.resolve())],
    }
    rows: dict[str, Any] = {"sdk": str(sdk), "state": str(home)}

    def launch(client: httpx.Client, label: str, **extra: Any) -> None:
        take_requests()
        body = {
            "workspace": {
                "kind": "LocalWorkspace",
                "working_dir": str(home / "workspace"),
            },
            "autotitle": True,
            "initial_message": {
                "role": "user",
                "content": [{"type": "text", "text": MESSAGE}],
                "run": True,
            },
            **extra,
        }
        response = client.post("/api/conversations", json=body)
        response.raise_for_status()
        conversation_id = response.json()["id"]
        info: dict[str, Any] = {}
        for _ in range(120):
            info = client.get(f"/api/conversations/{conversation_id}").json()
            if info.get("title") and info["execution_status"] not in ("running",):
                break
            time.sleep(0.5)
        time.sleep(1)
        rows[label] = {
            "agent.llm": {
                k: info["agent"]["llm"].get(k) for k in ("model", "usage_id")
            },
            "title": info.get("title"),
            "provider requests": take_requests(),
        }

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

        client.patch(
            "/api/settings", json={"agent_settings_diff": acp}
        ).raise_for_status()
        client.post(
            "/api/profiles/work",
            json={
                "llm": {
                    "model": "openai/gpt-4o-mini",
                    "api_key": PROFILE_KEY,
                    "base_url": fake_url,
                },
                "include_secrets": True,
            },
        ).raise_for_status()
        activated = client.post("/api/profiles/work/activate").json()
        settings = client.get(
            "/api/settings", headers={"X-Expose-Secrets": "plaintext"}
        ).json()
        stored = (home / "persistence" / "settings.json").read_text()
        rows["activate LLM profile on ACP settings"] = {
            "llm_applied": activated.get("llm_applied"),
            "GET /api/settings agent_settings.llm": settings["agent_settings"]
            .get("llm", {})
            .get("api_key"),
            "settings.json has an llm": '"llm"'
            in json.dumps(json.loads(stored)["agent_settings"]),
        }

        canvas_settings = {
            k: v for k, v in settings["agent_settings"].items() if k != "llm"
        }
        launch(client, "1. canvas-style launch", agent_settings=canvas_settings)
        launch(
            client,
            "2. launch from GET /api/settings",
            agent_settings=settings["agent_settings"],
        )
        launch(
            client,
            "3. launch with title_llm_profile",
            agent_settings=canvas_settings,
            title_llm_profile="work",
        )
    finally:
        server.send_signal(signal.SIGINT)
        try:
            server.wait(timeout=60)
        except subprocess.TimeoutExpired:
            server.kill()

    take_requests()
    script = textwrap.dedent(
        f"""
        import json
        from openhands.sdk import Conversation
        from openhands.sdk.event.llm_convertible import MessageEvent
        from openhands.sdk.llm import Message, TextContent
        from openhands.sdk.settings import ACPAgentSettings

        agent = ACPAgentSettings(
            acp_server="custom", acp_command=["true"], acp_model="claude-opus-4-7"
        ).create_agent()
        conv = Conversation(
            agent=agent, workspace={str(home / "workspace")!r}, visualizer=None
        )
        conv.state.events.append(MessageEvent(
            source="user",
            llm_message=Message(role="user", content=[TextContent(text={MESSAGE!r})]),
        ))
        print(json.dumps({{"title": conv.generate_title(),
                          "agent.llm": {{"model": agent.llm.model,
                                         "usage_id": agent.llm.usage_id}}}}))
        """
    )
    sdk_run = subprocess.run(
        [python, "-c", script], env=env, capture_output=True, text=True, timeout=300
    )
    sdk_row: dict[str, Any]
    try:
        sdk_row = json.loads(sdk_run.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        sdk_row = {"error": sdk_run.stderr[-800:]}
    sdk_row["provider requests"] = take_requests()
    rows["4. SDK generate_title()"] = sdk_row
    fake.shutdown()

    print(json.dumps(rows, indent=2))
    no_call = [
        rows[label]
        for label in (
            "1. canvas-style launch",
            "2. launch from GET /api/settings",
            "4. SDK generate_title()",
        )
    ]
    profile_launch = rows["3. launch with title_llm_profile"]
    activation = rows["activate LLM profile on ACP settings"]
    verdicts = {
        "profile activation leaves ACP settings without an llm": (
            activation["llm_applied"] is False
            and not activation["settings.json has an llm"]
        ),
        "settings-built ACP agents keep the acp-managed LLM": all(
            row["agent.llm"]["usage_id"] == "acp-managed" for row in no_call
        ),
        "no title request without title_llm_profile": all(
            not row["provider requests"] and row["title"] == MESSAGE for row in no_call
        ),
        "title_llm_profile is still used": profile_launch["title"] == PROFILE_TITLE
        and [r["authorization"] for r in profile_launch["provider requests"]]
        == [f"Bearer {PROFILE_KEY}"],
    }
    print("\n# Verdicts")
    for name, ok in verdicts.items():
        print(f"- {'PASS' if ok else 'FAIL'}  {name}")
    if args.out:
        args.out.write_text(json.dumps({"rows": rows, "verdicts": verdicts}, indent=2))
    return 0 if all(verdicts.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
