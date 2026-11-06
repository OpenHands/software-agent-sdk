"""Actual SDK -> HTTP OpenAI substitute, never a LiteLLM service."""

import os

for key in list(os.environ):
    if key.startswith(("LMNR_", "OTEL_", "LLM_", "OPENAI_", "ANTHROPIC_")):
        del os.environ[key]
os.environ.update(
    LITELLM_LOCAL_MODEL_COST_MAP="True",
    OPENHANDS_SUPPRESS_BANNER="1",
    DO_NOT_TRACK="1",
    PYTHON_DOTENV_DISABLED="1",
)
import dotenv

dotenv.dotenv_values = lambda *a, **k: {}
import asyncio
import hashlib
import importlib.metadata
import inspect
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from pathlib import Path
from pydantic import SecretStr
from openhands.sdk import LLM
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.llm.utils.telemetry import Telemetry

requests = []
state = {"header": None, "cost": 0.04}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.path == "/v1/chat/completions"
        requests.append(
            {
                "model": body["model"],
                "stream": body.get("stream", False),
                "include_usage": body.get("stream_options", {}).get("include_usage"),
            }
        )
        self.send_response(200)
        streaming = body.get("stream", False)
        self.send_header(
            "Content-Type", "text/event-stream" if streaming else "application/json"
        )
        if state["header"] is not None:
            self.send_header("x-litellm-response-cost", state["header"])
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "total_tokens": 110,
            "cost": state["cost"],
        }
        base = {"id": "synthetic-http", "created": 1, "model": body["model"]}
        if streaming:
            chunks = [
                dict(
                    base,
                    object="chat.completion.chunk",
                    choices=[
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "ok"},
                            "finish_reason": None,
                        }
                    ],
                ),
                dict(
                    base,
                    object="chat.completion.chunk",
                    choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}],
                ),
                dict(base, object="chat.completion.chunk", choices=[], usage=usage),
            ]
            payload = (
                "".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
                + "data: [DONE]\n\n"
            )
        else:
            payload = json.dumps(
                dict(
                    base,
                    object="chat.completion",
                    choices=[
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    usage=usage,
                )
            )
        data = payload.encode()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = []
    try:
        for alias in ["gpt-luna", "cost-auto"]:
            for streaming in [False, True]:
                for use_async in [False, True]:
                    for header, usage_cost in [
                        (None, 0.04),
                        ("0", 0.04),
                        ("0.09", 0.04),
                        (None, 0.0),
                    ]:
                        state.update(header=header, cost=usage_cost)
                        llm = LLM(
                            model="openai/" + alias,
                            api_key=SecretStr("synthetic-test-only"),
                            base_url=f"http://127.0.0.1:{server.server_port}/v1",
                            stream=streaming,
                            num_retries=0,
                            max_output_tokens=32,
                            temperature=0,
                        )
                        messages = [
                            Message(
                                role="system",
                                content=[
                                    TextContent(text="Synthetic accounting probe.")
                                ],
                            ),
                            Message(role="user", content=[TextContent(text="Say ok.")]),
                        ]
                        tokens = []
                        callback = tokens.append if streaming else None
                        if use_async:
                            response = await llm.acompletion(
                                messages=messages, on_token=callback
                            )
                        else:
                            response = llm.completion(
                                messages=messages, on_token=callback
                            )
                        raw = response.raw_response
                        metric = llm.metrics
                        raw_cost = getattr(getattr(raw, "usage", None), "cost", None)
                        result = {
                            "alias": alias,
                            "stream": streaming,
                            "async": use_async,
                            "sent_header": header,
                            "reported_cost": usage_cost,
                            "assembled_cost": raw_cost,
                            "selected_headers": raw._hidden_params.get(
                                "additional_headers", {}
                            ),
                            "accumulated_cost": metric.accumulated_cost,
                            "cost_records": len(metric.costs),
                            "prompt_tokens": metric.token_usages[0].prompt_tokens,
                            "stream_callbacks": len(tokens),
                        }
                        assert raw_cost == usage_cost, result
                        assert result["prompt_tokens"] == 100
                        if streaming:
                            assert requests[-1]["include_usage"] is True
                            assert tokens
                        if "--candidate" in sys.argv:
                            selected = result["selected_headers"].get(
                                "llm_provider-x-litellm-response-cost"
                            )
                            expected = (
                                float(selected) if selected is not None else usage_cost
                            )
                            assert metric.accumulated_cost == expected, result
                            expected_records = int(selected is not None or usage_cost > 0)
                            assert len(metric.costs) == expected_records, result
                        results.append(result)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    report = {
        "versions": {
            n: importlib.metadata.version(n) for n in ["openhands-sdk", "litellm"]
        },
        "telemetry_sha256": hashlib.sha256(
            Path(inspect.getfile(Telemetry)).read_bytes()
        ).hexdigest(),
        "scope": "Real HTTP with scripted provider on loopback; no proxy, customer endpoint, or live inference",
        "requests": len(requests),
        "results": results,
    }
    print("OHE3895_RESULTS=" + json.dumps(report))


asyncio.run(main())
