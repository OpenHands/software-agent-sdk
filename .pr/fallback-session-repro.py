"""Exercise fallback accounting through real SDK conversations and loopback HTTP.

Run with an isolated OH_PERSISTENCE_DIR and the checkout's Python environment:
    OH_PERSISTENCE_DIR=/tmp/fallback-repro-state uv run python \
        .pr/fallback-session-repro.py

The provider is simulated: primary requests receive HTTP 503, while successful
fallback requests report exactly 100 input and 5 output tokens. SDK execution,
settings, profile loading, HTTP transport, telemetry and accounting are real.
"""

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from openhands.sdk import LLM, Conversation
from openhands.sdk.llm.fallback_strategy import FallbackStrategy
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.settings import OpenHandsAgentSettings


def token_totals(conversation: Conversation) -> dict[str, int]:
    return {
        role: metrics.accumulated_token_usage.prompt_tokens
        if metrics.accumulated_token_usage is not None
        else 0
        for role, metrics in conversation.conversation_stats.usage_to_metrics.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assert-fixed", action="store_true")
    args = parser.parse_args()
    if not os.environ.get("OH_PERSISTENCE_DIR"):
        parser.error("Set OH_PERSISTENCE_DIR to an isolated temporary directory")

    requests: list[dict[str, Any]] = []

    class Provider(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            model = body["model"]
            failed = model == "gpt-4o"
            status = 503 if failed else 200
            requests.append({"model": model, "status": status})
            if failed:
                payload = {"error": {"message": "Simulated outage", "type": "server"}}
            else:
                payload = {
                    "id": f"chatcmpl-{len(requests)}",
                    "object": "chat.completion",
                    "created": 1,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "Done."},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 5,
                        "total_tokens": 105,
                    },
                }
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    evidence: list[dict[str, Any]] = []
    try:
        with TemporaryDirectory(prefix="fallback-repro-") as directory:
            root = Path(directory)
            common = {
                "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                "api_key": "loopback-only",
                "num_retries": 1,
                "stream": False,
                "input_cost_per_token": 0.001,
                "output_cost_per_token": 0.002,
            }
            profiles = root / "profiles"
            LLMProfileStore(profiles).save(
                "fallback",
                LLM(model="openai/gpt-4o-mini", **common),
                include_secrets=True,
            )
            primary = LLM(
                model="openai/gpt-4o",
                fallback_strategy=FallbackStrategy(
                    fallback_llms=["fallback"], profile_store_dir=profiles
                ),
                **common,
            )
            settings = OpenHandsAgentSettings(llm=primary, tools=[])
            conversation = Conversation(
                agent=settings.create_agent(),
                workspace=root / "workspace",
                persistence_dir=root / "conversations",
                visualizer=None,
            )
            try:
                for operation, expected_role in [
                    ("run-1", "default"),
                    ("run-2", "default"),
                    ("run-3", "default"),
                    ("ask_agent", "ask-agent-llm"),
                    ("condense", "condenser"),
                ]:
                    before = token_totals(conversation)
                    start = len(requests)
                    if operation.startswith("run"):
                        conversation.send_message(f"Reply briefly for {operation}.")
                        conversation.run()
                    elif operation == "ask_agent":
                        conversation.ask_agent("What are you doing?")
                    else:
                        conversation.condense()
                    after = token_totals(conversation)
                    deltas = {
                        role: total - before.get(role, 0)
                        for role, total in after.items()
                        if total != before.get(role, 0)
                    }
                    receipts = requests[start:]
                    successes = sum(item["status"] == 200 for item in receipts)
                    expected = {expected_role: 100 * successes}
                    evidence.append(
                        {
                            "operation": operation,
                            "http_requests": receipts,
                            "expected_prompt_token_deltas": expected,
                            "actual_prompt_token_deltas": deltas,
                            "correct": successes == 1 and deltas == expected,
                        }
                    )
            finally:
                conversation.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
    print(json.dumps(evidence, indent=2))
    if args.assert_fixed and not all(item["correct"] for item in evidence):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
