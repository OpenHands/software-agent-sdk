"""Run an offline-provider SDK conversation, persist it, and continue it.

Run against each built wheel with --expect-total 0.08. The baseline must fail
that assertion; the candidate must pass. No provider credentials are required.
"""

import argparse
import hashlib
import importlib.metadata
import inspect
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

# This standalone evidence script must not inherit developer telemetry or .env.
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

dotenv.dotenv_values = lambda *args, **kwargs: {}

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.llm.utils.telemetry import Telemetry

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--expect-total", type=float, default=0.08)
args = parser.parse_args()
requests = []


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.path == "/v1/chat/completions"
        requests.append({"model": body["model"], "stream": body.get("stream", False)})
        response = {
            "id": f"accounting-{len(requests)}",
            "object": "chat.completion",
            "created": 1,
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": f"finish-{len(requests)}",
                                "type": "function",
                                "function": {
                                    "name": "finish",
                                    "arguments": json.dumps(
                                        {"message": "Synthetic task completed."}
                                    ),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
                "cost": 0.04,
            },
        }
        payload = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
thread = Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    with TemporaryDirectory(prefix="ohe3895-") as tmp:
        workspace = Path(tmp) / "work"
        workspace.mkdir()
        persistence = Path(tmp) / "state"

        def make_agent():
            return Agent(
                llm=LLM(
                    model="openai/cost-auto",
                    api_key=SecretStr("synthetic-only"),
                    base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    usage_id="reported-cost",
                    num_retries=0,
                    max_output_tokens=32,
                    stream=False,
                    native_tool_calling=True,
                ),
                tools=[],
            )

        first = Conversation(
            agent=make_agent(),
            workspace=workspace,
            persistence_dir=persistence,
            delete_on_close=False,
            visualizer=None,
            max_iteration_per_run=2,
        )
        first.send_message("Complete this synthetic accounting task.")
        first.run()
        conversation_id = first.id
        first_total = first.conversation_stats.get_combined_metrics().accumulated_cost
        first_status = str(first.state.execution_status)
        first.close()
        resumed = Conversation(
            agent=make_agent(),
            workspace=workspace,
            persistence_dir=persistence,
            conversation_id=conversation_id,
            delete_on_close=False,
            visualizer=None,
            max_iteration_per_run=2,
        )
        restored_total = (
            resumed.conversation_stats.get_combined_metrics().accumulated_cost
        )
        resumed.send_message("Complete one more synthetic accounting task.")
        resumed.run()
        final_metrics = resumed.conversation_stats.get_combined_metrics()
        final_total = final_metrics.accumulated_cost
        final_status = str(resumed.state.execution_status)
        resumed.close()
        result = {
            "sdk_version": importlib.metadata.version("openhands-sdk"),
            "litellm_version": importlib.metadata.version("litellm"),
            "telemetry_sha256": hashlib.sha256(
                Path(inspect.getfile(Telemetry)).read_bytes()
            ).hexdigest(),
            "requests": len(requests),
            "reported_cost_per_request": 0.04,
            "first_total": first_total,
            "restored_total": restored_total,
            "final_total": final_total,
            "first_status": first_status,
            "final_status": final_status,
            "expected_total": args.expect_total,
            "scope": "Real SDK Conversation, HTTP substitute, disk persistence and continuation; no live inference",
        }
        print("OHE3895_DEMO=" + json.dumps(result), flush=True)
        assert len(requests) == 2, result
        assert restored_total == first_total, result
        assert final_total == args.expect_total, result
finally:
    server.shutdown()
    server.server_close()
    thread.join()
