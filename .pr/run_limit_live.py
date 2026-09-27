"""Exercise an unmodified Agent Server subprocess over HTTP, without credentials.

uv run python .pr/run_limit_live.py --checkout /path/to/checkout \
    --expect exceeded --output .pr/before.json

The local OpenAI-compatible HTTP fixture delays each completion and counts
actual overlapping requests from ordinary agents. Only the model provider is
substituted; no Agent Server, agent, conversation, or concurrency code is patched.
"""

import argparse
import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID, uuid4

import httpx

from openhands.sdk import LLM, Agent, TextContent
from openhands.sdk.conversation.request import (
    SendMessageRequest,
    StartConversationRequest,
)
from openhands.sdk.workspace import LocalWorkspace


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def exercise(args):
    samples = []
    active = peak = 0
    lock = threading.Lock()

    class ModelHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            nonlocal active, peak
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.path == "/v1/chat/completions", self.path
            assert not request.get("stream"), (
                "This fixture uses nonstreaming completions"
            )
            with lock:
                active += 1
                peak = max(peak, active)
                samples.append(
                    {"event": "enter", "time": time.monotonic(), "active": active}
                )
            time.sleep(2)
            response = json.dumps(
                {
                    "id": "live-admission-evidence",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": "gpt-4o",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "Done."},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            ).encode()
            with lock:
                active -= 1
                samples.append(
                    {"event": "exit", "time": time.monotonic(), "active": active}
                )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    provider = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    thread = threading.Thread(target=provider.serve_forever)
    thread.start()
    checkout = args.checkout.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    log_path = args.output.with_suffix(".server.log")
    try:
        with tempfile.TemporaryDirectory(prefix="run-limit-live-") as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            config = {
                "max_concurrent_runs": args.limit,
                "conversation_runtime": "local",
                "conversations_path": str(root / "conversations"),
                "workspace_path": str(workspace),
                "session_api_keys": [],
                "enable_vscode": False,
                "preload_tools": False,
            }
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config))
            port = free_port()
            url = f"http://127.0.0.1:{port}"
            env = {
                "PATH": os.environ["PATH"],
                "HOME": str(root),
                "PYTHONPATH": os.pathsep.join(
                    str(checkout / package)
                    for package in (
                        "openhands-agent-server",
                        "openhands-sdk",
                        "openhands-tools",
                        "openhands-workspace",
                    )
                ),
                "OPENHANDS_AGENT_SERVER_CONFIG_PATH": str(config_path),
                "OH_PERSISTENCE_DIR": str(root / "persistence"),
                "OPENHANDS_SUPPRESS_BANNER": "1",
                "LITELLM_LOCAL_MODEL_COST_MAP": "True",
            }
            command = [
                sys.executable,
                "-m",
                "openhands.agent_server",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ]
            with log_path.open("w") as log:
                process = subprocess.Popen(
                    command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT
                )
                try:
                    async with httpx.AsyncClient(base_url=url, timeout=30) as client:
                        async with asyncio.timeout(90):
                            while True:
                                if process.poll() is not None:
                                    raise RuntimeError(f"Server exited; see {log_path}")
                                try:
                                    if (await client.get("/health")).is_success:
                                        break
                                except httpx.TransportError:
                                    pass
                                await asyncio.sleep(0.1)
                        llm = LLM(
                            model="openai/gpt-4o",
                            base_url=f"http://127.0.0.1:{provider.server_port}/v1",
                            api_key="fixture",
                            stream=False,
                            num_retries=0,
                        )
                        ids = [str(uuid4()) for _ in range(args.conversations)]
                        requests = {}
                        for i, cid in enumerate(ids):
                            message = SendMessageRequest(
                                content=[
                                    TextContent(text=f"Say done for conversation {i}.")
                                ],
                                run=args.entrypoint == "create",
                            )
                            request = StartConversationRequest(
                                conversation_id=cid,
                                agent=Agent(llm=llm, tools=[]),
                                workspace=LocalWorkspace(working_dir=str(workspace)),
                                initial_message=message
                                if args.entrypoint == "create"
                                else None,
                                autotitle=False,
                            )
                            requests[cid] = request.model_dump(
                                mode="json", context={"expose_secrets": True}
                            )
                            if args.entrypoint == "run":
                                response = await client.post(
                                    "/api/conversations", json=requests[cid]
                                )
                                response.raise_for_status()
                                response = await client.post(
                                    f"/api/conversations/{cid}/events",
                                    json=message.model_dump(mode="json"),
                                )
                                response.raise_for_status()

                        async def submit(cid):
                            if args.entrypoint == "create":
                                return await client.post(
                                    "/api/conversations", json=requests[cid]
                                )
                            return await client.post(f"/api/conversations/{cid}/run")

                        responses = await asyncio.gather(*(submit(cid) for cid in ids))
                        initial_codes = [r.status_code for r in responses]
                        pending = [
                            cid
                            for cid, r in zip(ids, responses)
                            if r.status_code == 429
                        ]
                        for response in responses:
                            if response.status_code != 429:
                                response.raise_for_status()
                        if args.expect == "respected":
                            assert len(pending) == args.conversations - args.limit, (
                                initial_codes
                            )
                            if args.entrypoint == "create":
                                for cid in pending:
                                    response = await client.get(
                                        f"/api/conversations/{cid}"
                                    )
                                    assert response.status_code == 404, response.text
                                    assert not (
                                        root / "conversations" / UUID(cid).hex
                                    ).exists(), cid
                        else:
                            assert not pending, initial_codes
                        attempts = [initial_codes]
                        async with asyncio.timeout(60):
                            while True:
                                if pending:
                                    retried = await asyncio.gather(
                                        *(submit(cid) for cid in pending)
                                    )
                                    attempts.append([r.status_code for r in retried])
                                    for response in retried:
                                        if response.status_code != 429:
                                            response.raise_for_status()
                                    pending = [
                                        cid
                                        for cid, r in zip(pending, retried)
                                        if r.status_code == 429
                                    ]
                                responses = await asyncio.gather(
                                    *(
                                        client.get(f"/api/conversations/{cid}")
                                        for cid in ids
                                    )
                                )
                                for cid, response in zip(ids, responses):
                                    if cid not in pending:
                                        response.raise_for_status()
                                statuses = [
                                    "not_admitted"
                                    if cid in pending
                                    else response.json()["execution_status"]
                                    for cid, response in zip(ids, responses)
                                ]
                                assert not any(
                                    s in ("error", "stuck") for s in statuses
                                ), statuses
                                if all(s == "finished" for s in statuses):
                                    break
                                await asyncio.sleep(0.25)
                        assert len(samples) == 2 * args.conversations, samples
                        assert active == 0
                        assert (peak > args.limit) == (args.expect == "exceeded"), (
                            peak,
                            args.limit,
                            args.expect,
                        )
                        result = {
                            "revision": subprocess.check_output(
                                ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
                            ).strip(),
                            "diff_sha256": hashlib.sha256(
                                subprocess.check_output(
                                    ["git", "diff", "HEAD"], cwd=checkout
                                )
                            ).hexdigest(),
                            "config": config,
                            "server_pid": process.pid,
                            "server_url": url,
                            "limit": args.limit,
                            "entrypoint": args.entrypoint,
                            "initial_http_statuses": initial_codes,
                            "attempt_http_statuses": attempts,
                            "conversations": ids,
                            "peak_active_llm_requests": peak,
                            "final_statuses": statuses,
                            "expected": args.expect,
                            "samples": samples,
                        }
                        args.output.write_text(json.dumps(result, indent=2) + "\n")
                        print(
                            f"PASS: expected={args.expect}, limit={args.limit}, "
                            f"peak={peak}, "
                            f"completed={len(ids)}; {args.output}"
                        )
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--expect", choices=["exceeded", "respected"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--entrypoint", choices=["run", "create"], default="run")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--conversations", type=int, default=6)
    asyncio.run(exercise(parser.parse_args()))
