"""Local stand-ins for the infrastructure a workspace program talks to.

- `AgentServer`: the repo's `openhands.agent_server` as a subprocess on a free
  localhost port, started in a directory of the run and stopped on exit (what
  `DockerWorkspace` runs inside a container, without the container).
- `FakeLLMServer`: an OpenAI-compatible `/v1/chat/completions` endpoint that
  answers with scripted steps (the same step format as `sdk_verify` scripts),
  so an agent running *inside* the agent server can be scripted: a `TestLLM`
  does not survive serialization to the server, an `LLM(base_url=...)` does.
- `CaptureServer`: records every request (method, path, headers, JSON body)
  and answers 200, for callbacks and header checks.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class AgentServer:
    def __init__(self, cwd: Path, env: dict[str, str] | None = None) -> None:
        self.port = free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.cwd = cwd
        self.env = env or {}
        self.proc: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> AgentServer:
        self.cwd.mkdir(parents=True, exist_ok=True)
        log = (self.cwd / "agent-server.log").open("ab")
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "openhands.agent_server",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            cwd=self.cwd,
            env={**os.environ, "LOG_JSON": "true", **self.env},
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"agent server exited {self.proc.returncode}")
            try:
                if httpx.get(f"{self.base_url}/health", timeout=1).status_code == 200:
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        raise RuntimeError("agent server did not become healthy in 60s")

    def stop(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()

    def __exit__(self, *exc: object) -> None:
        self.stop()


class _Server:
    handler_cls: type[BaseHTTPRequestHandler]

    def __enter__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self.handler_cls)
        self.httpd.owner = self  # type: ignore[attr-defined]
        self.port = self.httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class _CaptureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def _record(self) -> None:
        owner = self.server.owner  # type: ignore[attr-defined]
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = raw.decode(errors="replace")
        owner.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body,
            }
        )
        status, payload = owner.respond(self.command, self.path)
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_DELETE = _record


class CaptureServer(_Server):
    handler_cls = _CaptureHandler

    def __init__(self, routes: dict[str, tuple[int, Any]] | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.routes = routes or {}

    def respond(self, _method: str, path: str) -> tuple[int, Any]:
        """A route is (status, body), or a list of them answered in turn
        (the last one repeats)."""
        route = self.routes.get(path.split("?")[0], (200, {"ok": True}))
        if isinstance(route, list):
            return route.pop(0) if len(route) > 1 else route[0]
        return route


class _LLMHandler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        owner = self.server.owner  # type: ignore[attr-defined]
        length = int(self.headers.get("content-length") or 0)
        request = json.loads(self.rfile.read(length) or b"{}")
        owner.requests.append(request)
        # Side calls without tools (conversation title generation and the
        # like) get plain text and do not consume the agent's script.
        step = owner.next_step() if request.get("tools") else {"text": "Scripted title"}
        data = json.dumps(owner.completion(step, request)).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        if owner.cost is not None:
            self.send_header("x-litellm-response-cost", str(owner.cost))
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FakeLLMServer(_Server):
    """Answers chat completions with scripted steps, then plain text.

    `cost` adds the `x-litellm-response-cost` header a LiteLLM proxy sends;
    `cached_tokens` reports prompt-cache reads in the usage block."""

    handler_cls = _LLMHandler

    def __init__(
        self,
        script: list[dict[str, Any]],
        cost: float | None = None,
        cached_tokens: int = 0,
    ) -> None:
        self.cost = cost
        self.cached_tokens = cached_tokens
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def next_step(self) -> dict[str, Any]:
        with self.lock:
            return self.script.pop(0) if self.script else {"text": "Scripted answer"}

    def completion(self, step: dict[str, Any], request: dict[str, Any]) -> Any:
        calls: list[dict[str, Any]] = []
        if "finish" in step:
            calls = [{"tool": "finish", "args": {"message": step["finish"]}}]
        elif "tool" in step:
            calls = [step]
        elif "tools" in step:
            calls = step["tools"]
        n = len(self.requests)
        message: dict[str, Any] = {"role": "assistant", "content": step.get("text", "")}
        if calls:
            message["tool_calls"] = [
                {
                    "id": f"call_{n}_{i}",
                    "type": "function",
                    "function": {
                        "name": c["tool"],
                        "arguments": json.dumps(c.get("args", {})),
                    },
                }
                for i, c in enumerate(calls)
            ]
        return {
            "id": f"chatcmpl-{n}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.get("model", "fake"),
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if calls else "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
                "prompt_tokens_details": {"cached_tokens": self.cached_tokens},
            },
        }

    def llm(self, **kwargs: Any) -> Any:
        """An `LLM` pointed at this endpoint; it serializes to the server."""
        from pydantic import SecretStr

        from openhands.sdk import LLM

        return LLM(
            model="openai/fake-scripted-model",
            base_url=f"{self.base_url}/v1",
            api_key=SecretStr("fake-key"),
            usage_id=kwargs.pop("usage_id", "agent"),
            num_retries=0,
            **kwargs,
        )
