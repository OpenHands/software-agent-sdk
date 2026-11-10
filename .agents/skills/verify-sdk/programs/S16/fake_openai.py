"""A local OpenAI-compatible endpoint that answers every chat completion
with a fixed reply, so a saved LLM profile can point at it without a key or
network. `serve(reply)` returns the base URL; `requests` records each body.
"""

from __future__ import annotations

import http.server
import json
import threading
import time
from typing import Any


requests: list[dict[str, Any]] = []


def serve(reply: str) -> str:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            payload = {
                "id": f"chatcmpl-{len(requests)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.get("model", "fake"),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": reply},
                    }
                ],
                "usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 7,
                    "total_tokens": 57,
                },
            }
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/v1"
