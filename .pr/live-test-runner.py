"""Reproduce the live OpenAI gateway smoke-test artifacts.

Run from the repository root after exporting real credentials:

    OPENAI_API_KEY=... LITELLM_API_KEY=... \
      uv run python .pr/live-test-runner.py

The script starts a local agent-server, creates two profiles, then calls the
new gateway through the OpenAI SDK's chat.completions API.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI


ROOT = Path.cwd()
ARTIFACTS_DIR = ROOT / ".pr"
SESSION_KEY = "live-test-session-key"
LITELLM_PROXY_BASE_URL = "https://llm-proxy.eval.all-hands.dev"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _wait_for_health(base_url: str, proc: subprocess.Popen[str]) -> None:
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/health", timeout=2)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if proc.poll() is not None:
            raise RuntimeError(f"agent-server exited with {proc.returncode}")
        time.sleep(0.25)
    raise RuntimeError("agent-server did not become healthy")


def _server_env(tmp: Path, base_url: str) -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("OH_WEBHOOKS_"):
            env.pop(key)
    env.update(
        {
            "HOME": str(tmp / "home"),
            "OH_BASH_EVENTS_DIR": str(tmp / "bash_events"),
            "OH_CONVERSATIONS_PATH": str(tmp / "conversations"),
            "OH_ENABLE_VNC": "0",
            "OH_ENABLE_VSCODE": "0",
            "OH_INTERNAL_SERVER_URL": base_url,
            "OH_PRELOAD_TOOLS": "0",
            "OH_SECRET_KEY": "live-test-secret-key",
            "OH_SESSION_API_KEYS_0": SESSION_KEY,
            "OH_WEBHOOKS": "[]",
            "TMUX_TMPDIR": str(tmp / "tmux"),
        }
    )
    return env


def _save_profile(
    client: httpx.Client,
    base_url: str,
    name: str,
    llm: dict[str, str],
) -> None:
    response = client.post(
        f"{base_url}/api/profiles/{name}",
        headers={"X-Session-API-Key": SESSION_KEY},
        json={"llm": llm, "include_secrets": True},
    )
    response.raise_for_status()


def _run_completion(
    client: OpenAI,
    *,
    model: str,
    prompt: str,
) -> dict[str, Any]:
    started = time.time()
    raw_response = client.chat.completions.with_raw_response.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "Answer directly and do not use tools.",
            },
            {"role": "user", "content": prompt},
        ],
    )
    parsed = raw_response.parse()
    return {
        "conversation_id_header_present": bool(
            raw_response.headers.get("X-OpenHands-ServerConversation-ID")
        ),
        "elapsed_seconds": round(time.time() - started, 3),
        "response": parsed.model_dump(mode="json"),
        "status_code": raw_response.status_code,
    }


def main() -> None:
    openai_key = _require_env("OPENAI_API_KEY")
    litellm_key = _require_env("LITELLM_API_KEY")
    ARTIFACTS_DIR.mkdir(exist_ok=True)

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    with tempfile.TemporaryDirectory(prefix="oh-openai-gateway-live-") as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "home").mkdir()
        (tmp / "tmux").mkdir()

        server_log = ARTIFACTS_DIR / "live-server.log"
        with server_log.open("w") as log:
            proc = subprocess.Popen(
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "openhands.agent_server",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=ROOT,
                env=_server_env(tmp, base_url),
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                _wait_for_health(base_url, proc)

                http_client = httpx.Client(timeout=90)
                _save_profile(
                    http_client,
                    base_url,
                    "openai_nano",
                    {"api_key": openai_key, "model": "gpt-5-nano"},
                )
                _save_profile(
                    http_client,
                    base_url,
                    "haiku_eval_proxy",
                    {
                        "api_key": litellm_key,
                        "base_url": LITELLM_PROXY_BASE_URL,
                        "model": ("litellm_proxy/anthropic/claude-haiku-4-5-20251001"),
                    },
                )

                openai_client = OpenAI(
                    api_key=SESSION_KEY,
                    base_url=f"{base_url}/v1",
                    timeout=90,
                )
                models = openai_client.models.list()
                _write_json(
                    ARTIFACTS_DIR / "live-models.json",
                    models.model_dump(mode="json"),
                )
                _write_json(
                    ARTIFACTS_DIR / "live-openai-nano.json",
                    _run_completion(
                        openai_client,
                        model="openhands_openai_nano",
                        prompt=(
                            "In one sentence, explain what an OpenAI-compatible "
                            "agent-server gateway does."
                        ),
                    ),
                )
                _write_json(
                    ARTIFACTS_DIR / "live-litellm-haiku.json",
                    _run_completion(
                        openai_client,
                        model="openhands_haiku_eval_proxy",
                        prompt=(
                            "Write a haiku about a software agent opening an "
                            "OpenAI-compatible gateway."
                        ),
                    ),
                )
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)


if __name__ == "__main__":
    main()
