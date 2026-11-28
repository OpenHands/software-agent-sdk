"""Exercise SDK retries over loopback HTTP with real waits and finite capacity."""

import argparse
import asyncio
import concurrent.futures
import hashlib
import json
import random
import re
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import openhands.sdk.llm.utils.retry_mixin as retry_module
from openhands.sdk import LLM, Message, TextContent


class CapacityServer(ThreadingHTTPServer):
    request_queue_size = 128


def run_trial(method, seed, jitter):
    lock = threading.Lock()
    active = 0
    events = []
    attempts = Counter()
    start = time.monotonic()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            nonlocal active
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            match = re.search(r"client-(\d+)", json.dumps(data))
            assert match is not None
            client = match.group(1)
            with lock:
                attempts[client] += 1
                admitted = active < 4
                if admitted:
                    active += 1
                events.append({"client": client, "attempt": attempts[client],
                               "at": time.monotonic() - start,
                               "status": 200 if admitted else 503,
                               "path": self.path})
            if admitted:
                time.sleep(0.3)
                with lock:
                    active -= 1
                if self.path.endswith("/responses"):
                    body = {"id": "resp_probe", "object": "response",
                            "created_at": int(time.time()), "status": "completed",
                            "model": data["model"], "error": None,
                            "output": [{"id": "msg_probe", "type": "message",
                                        "status": "completed", "role": "assistant",
                                        "content": [{"type": "output_text", "text": "ok",
                                                     "annotations": []}]}],
                            "usage": {"input_tokens": 2, "output_tokens": 1,
                                      "total_tokens": 3}}
                else:
                    body = {"id": "chatcmpl-probe", "object": "chat.completion",
                            "created": int(time.time()), "model": data["model"],
                            "choices": [{"index": 0, "finish_reason": "stop",
                                         "message": {"role": "assistant", "content": "ok"}}],
                            "usage": {"prompt_tokens": 2, "completion_tokens": 1,
                                      "total_tokens": 3}}
            else:
                body = {"error": {"message": "capacity occupied", "type": "server_error",
                                  "code": "overloaded"}}
            encoded = json.dumps(body).encode()
            self.send_response(200 if admitted else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = CapacityServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.start()
    models = []
    random.seed(seed)
    for i in range(24):
        config = dict(model="openai/gpt-4o", api_key="local-fixture-only",
                      base_url=f"http://127.0.0.1:{server.server_port}/v1",
                      num_retries=4, retry_multiplier=1, retry_min_wait=1,
                      retry_max_wait=4, timeout=20, usage_id=f"probe-{i}")
        if jitter is not None:
            config["retry_jitter"] = jitter
        models.append(LLM(**config))
    def messages(i):
        return [Message(role="system", content=[TextContent(text="Return ok.")]),
                Message(role="user", content=[TextContent(text=f"client-{i}")])]

    def invoke(i):
        try:
            response = getattr(models[i], method)(messages=messages(i))
            assert response.message.content[0].text == "ok"
            return {"client": str(i), "ok": True, "done": time.monotonic() - start}
        except Exception as error:
            return {"client": str(i), "ok": False, "error": type(error).__name__,
                    "done": time.monotonic() - start}

    async def ainvoke(i, gate):
        await gate.wait()
        try:
            response = await getattr(models[i], method)(messages=messages(i))
            assert response.message.content[0].text == "ok"
            return {"client": str(i), "ok": True, "done": time.monotonic() - start}
        except Exception as error:
            return {"client": str(i), "ok": False, "error": type(error).__name__,
                    "done": time.monotonic() - start}

    async def async_batch():
        gate = asyncio.Event()
        tasks = [asyncio.create_task(ainvoke(i, gate)) for i in range(24)]
        gate.set()
        return await asyncio.gather(*tasks)

    try:
        if method.startswith("a"):
            results = asyncio.run(async_batch())
        else:
            barrier = threading.Barrier(24)

            def call(i):
                barrier.wait()
                return invoke(i)

            with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
                results = list(pool.map(call, range(24)))
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()
    retry_times = sorted(e["at"] for e in events if e["attempt"] > 1)
    assert len(attempts) == 24
    peak = max((sum(t <= x < t + 0.1 for x in retry_times) for t in retry_times), default=0)
    return {"method": method, "seed": seed, "jitter": jitter,
            "capacity": 4, "service_seconds": 0.3, "clients": 24,
            "config": {"num_retries": 4, "multiplier": 1, "min": 1, "max": 4},
            "peak_retries_in_100ms": peak,
            "failures": sum(not r["ok"] for r in results),
            "elapsed_seconds": max(r["done"] for r in results),
            "http_attempts": len(events), "events": events, "results": results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--methods", nargs="+", default=["completion", "acompletion", "responses", "aresponses"])
    parser.add_argument("--seeds", nargs="+", type=int, default=[11, 29, 47])
    parser.add_argument("--jitter", type=float)
    args = parser.parse_args()
    source = Path(retry_module.__file__)
    report = {"source": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "limits": "Synthetic capacity-limited HTTP service, real SDK/LiteLLM transport and sleep; scaled waits, no model inference or production throughput claim.",
              "trials": []}
    for method in args.methods:
        for seed in args.seeds:
            trial = run_trial(method, seed, args.jitter)
            report["trials"].append(trial)
            args.output.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps({k: v for k, v in trial.items() if k not in ("events", "results")}), flush=True)


if __name__ == "__main__":
    main()
