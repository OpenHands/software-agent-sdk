"""Benchmark how long the agent-server takes to start a conversation.

Each round starts a real agent-server, creates conversations the way Agent
Canvas does (agent profile, first message in the create request), and measures
the create request, the first agent event, and how long the event loop stalls
meanwhile. A mock OpenAI-compatible LLM answers instantly, so the numbers are
pure agent-server overhead.

    # one interpreter
    python conversation_start.py run --python .venv/bin/python
    # base vs head, interleaved, Markdown report
    python conversation_start.py compare --base-python base/.venv/bin/python \\
        --head-python .venv/bin/python --report report.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import websockets


SESSION_KEY = "benchmark-session-key"
REPORT_MARKER = "<!-- agent-server-conversation-start-benchmark -->"


class _MockLLM(BaseHTTPRequestHandler):
    """Answer every chat completion instantly: a `finish` call, or a title."""

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        message: dict = {"role": "assistant", "content": "Benchmark title"}
        if body.get("tools"):
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_{uuid.uuid4().hex[:12]}",
                        "type": "function",
                        "function": {
                            "name": "finish",
                            "arguments": json.dumps({"message": "done"}),
                        },
                    }
                ],
            }
        common = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "created": int(time.time()),
            "model": body.get("model", "mock"),
        }
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        if body.get("stream"):
            delta = dict(message)
            for i, call in enumerate(delta.get("tool_calls") or []):
                call["index"] = i
            chunks = [
                {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": usage,
                },
            ]
            payload = (
                b"".join(
                    b"data: "
                    + json.dumps(
                        {**common, "object": "chat.completion.chunk", **c}
                    ).encode()
                    + b"\n\n"
                    for c in chunks
                )
                + b"data: [DONE]\n\n"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        payload = json.dumps(
            {
                **common,
                "object": "chat.completion",
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": usage,
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_alive(base: str, timeout: float = 300) -> float:
    start = time.perf_counter()
    async with httpx.AsyncClient() as client:
        while time.perf_counter() - start < timeout:
            try:
                if (await client.get(f"{base}/alive", timeout=2)).status_code == 200:
                    return time.perf_counter() - start
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.05)
    raise TimeoutError(f"agent-server at {base} did not come up")


async def _first_agent_event(url: str, timeout: float = 120) -> float:
    async with websockets.connect(url, max_size=None) as ws:
        while True:
            event = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            if event.get("source") == "agent" and event.get("kind") in (
                "MessageEvent",
                "ActionEvent",
            ):
                return time.perf_counter()


async def _run_server(python: str, creates: int, extensions_path: str | None) -> dict:
    """Start one agent-server from `python`, create conversations, return timings."""
    llm = ThreadingHTTPServer(("127.0.0.1", 0), _MockLLM)
    threading.Thread(target=llm.serve_forever, daemon=True).start()
    root = Path(tempfile.mkdtemp(prefix="oh-start-bench-", dir="/tmp"))
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("OH_", "LLM_", "SESSION_API"))
    }
    env.update(
        {
            "HOME": str(root / "home"),
            "OPENHANDS_SUPPRESS_BANNER": "1",
            "LOG_LEVEL": "WARNING",
            "OH_PERSISTENCE_DIR": str(root / "home"),
            "OH_CONVERSATIONS_PATH": str(root / "conversations"),
            "OH_BASH_EVENTS_DIR": str(root / "bash_events"),
            "OH_SECRET_KEY": "benchmark-secret-key-0123456789abcdef",
            "OH_SESSION_API_KEYS_0": SESSION_KEY,
            "OH_VSCODE_PORT": str(_free_port()),
            "TMUX_TMPDIR": str(root / "tmux"),
        }
    )
    if extensions_path:
        env["EXTENSIONS_PATH"] = extensions_path
    for sub in ("home", "tmux", "workspaces"):
        (root / sub).mkdir()
    log = open(root / "server.log", "w")
    server = subprocess.Popen(
        [
            os.path.abspath(python),
            "-m",
            "openhands.agent_server",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=root / "home",
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    headers = {"X-Session-API-Key": SESSION_KEY}
    result: dict = {"creates": []}
    try:
        result["startup_s"] = await _wait_alive(base)
        async with httpx.AsyncClient(base_url=base, headers=headers, timeout=300) as c:
            r = await c.post(
                "/api/profiles/benchmark",
                json={
                    "llm": {
                        "model": "openai/mock-model",
                        "base_url": f"http://127.0.0.1:{llm.server_address[1]}/v1",
                        "api_key": "sk-mock",
                    },
                    "include_secrets": True,
                },
            )
            r.raise_for_status()
            r = await c.post(
                "/api/agent-profiles/benchmark",
                json={"agent_kind": "openhands", "llm_profile_ref": "benchmark"},
            )
            r.raise_for_status()
            profiles = (await c.get("/api/agent-profiles")).json()["profiles"]
            profile_id = next(p["id"] for p in profiles if p["name"] == "benchmark")
            for i in range(creates):
                result["creates"].append(
                    await _create_one(c, base, port, root, profile_id, cold=i == 0)
                )
    finally:
        server.terminate()
        try:
            server.wait(30)
        except subprocess.TimeoutExpired:
            server.kill()
        llm.shutdown()
        log.close()
        if os.environ.get("BENCHMARK_KEEP_LOGS"):
            print(f"logs kept in {root}", file=sys.stderr)
        else:
            shutil.rmtree(root, ignore_errors=True)
    return result


async def _create_one(c, base, port, root, profile_id, *, cold: bool) -> dict:
    conversation_id = str(uuid.uuid4())
    working_dir = root / "workspaces" / conversation_id.replace("-", "")
    working_dir.mkdir()
    body = {
        "conversation_id": conversation_id,
        "agent_profile_id": profile_id,
        "workspace": {"kind": "LocalWorkspace", "working_dir": str(working_dir)},
        "worktree": True,
        "confirmation_policy": {"kind": "NeverConfirm"},
        "max_iterations": 500,
        "stuck_detection": True,
        "autotitle": True,
        "tags": {"clientsource": "benchmark"},
        "initial_message": {
            "role": "user",
            "content": [{"type": "text", "text": "hello"}],
            "run": True,
        },
    }
    stalls: list[float] = []
    done = asyncio.Event()

    async def probe() -> None:
        async with httpx.AsyncClient(base_url=base, timeout=300) as pc:
            while not done.is_set():
                t = time.perf_counter()
                await pc.get("/alive")
                stalls.append(time.perf_counter() - t)
                await asyncio.sleep(0.02)

    prober = asyncio.create_task(probe())
    start = time.perf_counter()
    r = await c.post("/api/conversations", json=body)
    create_s = time.perf_counter() - start
    done.set()
    await prober
    r.raise_for_status()
    ws = (
        f"ws://127.0.0.1:{port}/sockets/events/{conversation_id}"
        f"?resend_mode=all&session_api_key={SESSION_KEY}"
    )
    first_event_s = await _first_agent_event(ws) - start
    return {
        "cold": cold,
        "create_s": create_s,
        "first_agent_event_s": first_event_s,
        "max_alive_s": max(stalls) if stalls else 0.0,
    }


def _median_ci(xs: list[float], n: int = 2000) -> tuple[float, float, float]:
    rng = random.Random(0)
    boots = sorted(statistics.median(rng.choices(xs, k=len(xs))) for _ in range(n))
    return statistics.median(xs), boots[int(0.025 * n)], boots[int(0.975 * n)]


METRICS = [
    ("create_s", False, "Create conversation (warm)"),
    ("first_agent_event_s", False, "Create → first agent event (warm)"),
    ("create_s", True, "First create after server start"),
    ("max_alive_s", False, "Worst `/alive` latency during a create"),
]


def _samples(runs: list[dict], key: str, cold: bool) -> list[float]:
    return [c[key] for run in runs for c in run["creates"] if c["cold"] == cold]


def _verdict(base: float, head: float) -> str:
    delta = head - base
    if delta >= max(0.15, 0.25 * base):
        return "⚠️ slower"
    if -delta >= max(0.15, 0.25 * base):
        return "✅ faster"
    return "≈ same"


def render_report(base_runs: list[dict], head_runs: list[dict], meta: dict) -> str:
    rows = []
    for key, cold, label in METRICS:
        b = _samples(base_runs, key, cold)
        h = _samples(head_runs, key, cold)
        bm, blo, bhi = _median_ci(b)
        hm, hlo, hhi = _median_ci(h)
        rows.append(
            f"| {label} | {bm:.3f} s [{blo:.3f}, {bhi:.3f}] "
            f"| {hm:.3f} s [{hlo:.3f}, {hhi:.3f}] "
            f"| {(hm - bm) / bm:+.0%} | {_verdict(bm, hm)} |"
        )
    startup_b = statistics.median(r["startup_s"] for r in base_runs)
    startup_h = statistics.median(r["startup_s"] for r in head_runs)
    change = (startup_h - startup_b) / startup_b
    rows.append(
        f"| Server startup (`/alive`) | {startup_b:.2f} s | {startup_h:.2f} s "
        f"| {change:+.0%} | {_verdict(startup_b, startup_h)} |"
    )
    return "\n".join(
        [
            REPORT_MARKER,
            "### Agent-server conversation start benchmark",
            "",
            f"Base `{meta['base_ref']}` vs this PR `{meta['head_ref']}`, measured "
            "in the same job on the same runner, interleaved: "
            f"{meta['rounds']} server starts per side × {meta['creates']} creates "
            "each. Medians with 95% bootstrap CIs. The LLM is a mock that answers "
            "instantly, so this is agent-server overhead only.",
            "",
            "| | base | this PR | change | |",
            "|---|---|---|---|---|",
            *rows,
            "",
            "<details><summary>How this is measured</summary>",
            "",
            "Each create is a Canvas-shaped `POST /api/conversations` (agent profile, "
            "`worktree: true` on a fresh directory, first message included, so the "
            "request waits for agent startup). A side task polls `/alive` every 20 ms "
            "to catch event-loop stalls. A change is flagged only when the medians "
            "differ by at least 25% and 150 ms. Runner: "
            f"`{meta['runner']}`, {meta['env_vars']} environment variables. "
            "Run it locally with `python scripts/agent_server_benchmarks/"
            "conversation_start.py --help`.",
            "",
            "</details>",
        ]
    )


async def _cmd_run(args) -> None:
    for _ in range(args.rounds):
        print(
            json.dumps(
                await _run_server(args.python, args.creates, args.extensions_path)
            )
        )


async def _cmd_compare(args) -> None:
    base_runs, head_runs = [], []
    for i in range(args.rounds):
        order = [("base", args.base_python), ("head", args.head_python)]
        for side, python in order if i % 2 == 0 else order[::-1]:
            run = await _run_server(python, args.creates, args.extensions_path)
            (base_runs if side == "base" else head_runs).append(run)
            print(f"round {i + 1} {side}: {json.dumps(run)}", file=sys.stderr)
    meta = {
        "base_ref": args.base_ref,
        "head_ref": args.head_ref,
        "rounds": args.rounds,
        "creates": args.creates,
        "runner": os.environ.get("RUNNER_NAME", socket.gethostname()),
        "env_vars": len(os.environ),
    }
    report = render_report(base_runs, head_runs, meta)
    Path(args.report).write_text(report + "\n")
    Path(args.report).with_suffix(".json").write_text(
        json.dumps({"base": base_runs, "head": head_runs, "meta": meta}, indent=1)
    )
    print(report)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark agent-server conversation start."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--creates", type=int, default=8, help="creates per server start"
    )
    common.add_argument(
        "--rounds", type=int, default=3, help="server starts (per side)"
    )
    common.add_argument(
        "--extensions-path",
        default=os.environ.get("EXTENSIONS_PATH"),
        help="local OpenHands/extensions checkout, so public skills are not cloned",
    )
    run = sub.add_parser("run", parents=[common], help="benchmark one interpreter")
    run.add_argument("--python", default=sys.executable)
    compare = sub.add_parser("compare", parents=[common], help="base vs head report")
    compare.add_argument("--base-python", required=True)
    compare.add_argument("--head-python", required=True)
    compare.add_argument("--base-ref", default="base")
    compare.add_argument("--head-ref", default="head")
    compare.add_argument("--report", default="conversation-start-report.md")
    args = parser.parse_args()
    asyncio.run(_cmd_run(args) if args.command == "run" else _cmd_compare(args))


if __name__ == "__main__":
    main()
