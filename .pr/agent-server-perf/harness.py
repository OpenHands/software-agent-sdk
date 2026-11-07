"""Spawn a real agent-server + mock LLM as subprocesses and drive them over HTTP/WS."""

import asyncio
import contextlib
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
# Checkout whose .venv runs the server; defaults to the repo containing .pr/.
REPO = Path(os.environ.get("SDK_REPO", Path(__file__).resolve().parents[2]))
PY = str(REPO / ".venv/bin/python")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def wait_http(url: str, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as c:
        while time.monotonic() < deadline:
            with contextlib.suppress(Exception):
                r = await c.get(url, timeout=2)
                if r.status_code < 500:
                    return
            await asyncio.sleep(0.25)
    raise TimeoutError(url)


class Stack:
    def __init__(self, cpu_count: int | None = None, extra_env: dict | None = None,
                 stream: bool = False, tmp: Path | None = None):
        self.cpu_count = cpu_count
        self.extra_env = extra_env or {}
        self.stream = stream
        self.tmp = tmp or Path(tempfile.mkdtemp(prefix="ohbench-"))
        self.llm_port = free_port()
        self.port = free_port()
        self.procs: list[subprocess.Popen] = []

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws_base(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    async def __aenter__(self):
        env = {**os.environ, "OPENHANDS_SUPPRESS_BANNER": "1", **self.extra_env}
        env.pop("OH_SESSION_API_KEYS_0", None)
        env.pop("SESSION_API_KEY", None)
        llm_log = open(self.tmp / "llm.log", "w")
        self.procs.append(subprocess.Popen(
            [PY, str(HERE / "mock_llm.py"), str(self.llm_port)],
            stdout=llm_log, stderr=subprocess.STDOUT, env=env,
        ))
        srv_env = dict(env)
        if self.cpu_count:
            srv_env["PYTHON_CPU_COUNT"] = str(self.cpu_count)
        srv_env.setdefault("LOG_LEVEL", "WARNING")
        srv_log = open(self.tmp / "server.log", "w")
        (self.tmp / "work").mkdir(exist_ok=True)
        self.procs.append(subprocess.Popen(
            [PY, "-m", "openhands.agent_server", "--host", "127.0.0.1",
             "--port", str(self.port)],
            cwd=self.tmp / "work", stdout=srv_log, stderr=subprocess.STDOUT,
            env=srv_env,
        ))
        await wait_http(f"http://127.0.0.1:{self.llm_port}/stats")
        await wait_http(f"{self.base}/ready", timeout=180)
        self.client = httpx.AsyncClient(base_url=self.base, timeout=120)
        return self

    async def __aexit__(self, *exc):
        with contextlib.suppress(Exception):
            await self.client.aclose()
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            with contextlib.suppress(Exception):
                p.wait(timeout=10)
            if p.poll() is None:
                p.kill()

    def agent_spec(self) -> dict:
        return {
            "llm": {
                "model": "openai/mock",
                "base_url": f"http://127.0.0.1:{self.llm_port}/v1",
                "api_key": "sk-mock",
                "usage_id": "agent",
                "stream": self.stream,
                "num_retries": 0,
            },
            "tools": [{"name": "terminal"}],
        }

    async def create(self, text: str | None = None, run: bool = True) -> str:
        body = {
            "agent": self.agent_spec(),
            "workspace": {"working_dir": str(self.tmp / "ws")},
            "autotitle": False,
        }
        if text is not None:
            body["initial_message"] = {
                "role": "user", "content": [{"type": "text", "text": text}],
                "run": run,
            }
        r = await self.client.post("/api/conversations", json=body)
        r.raise_for_status()
        return r.json()["id"]

    async def status(self, cid: str) -> str:
        try:
            r = await self.client.get(f"/api/conversations/{cid}", timeout=5)
        except httpx.TimeoutException:
            return "unresponsive"
        r.raise_for_status()
        return r.json()["execution_status"]

    async def wait_status(self, cid: str, want: set[str], timeout: float) -> str | None:
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = await self.status(cid)
            if last in want:
                return last
            await asyncio.sleep(0.1)
        return None

    async def send(self, cid: str, text: str, run: bool = True) -> None:
        r = await self.client.post(
            f"/api/conversations/{cid}/events",
            json={"role": "user", "content": [{"type": "text", "text": text}], "run": run},
        )
        r.raise_for_status()


def log(*a):
    print(*a, flush=True, file=sys.stderr)
