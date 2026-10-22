"""Stub OpenAI chat-completions server for running examples without an LLM key.

First call: a terminal tool call that writes FACTS.txt. Later calls: finish.
Usage: python stub_llm.py PORT
"""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def tool_call(name: str, args: dict) -> dict:
    return {
        "id": f"call_{time.time_ns()}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        tools = {t["function"]["name"] for t in body.get("tools", [])}
        done = any(m.get("role") == "tool" for m in body["messages"])
        print(f"{self.path} tools={sorted(tools)} done={done}", flush=True)
        if done:
            call = tool_call("finish", {"message": "Wrote FACTS.txt."})
        else:
            call = tool_call(
                "terminal",
                {
                    "command": "printf 'fact 1\\nfact 2\\nfact 3\\n' > FACTS.txt",
                    "security_risk": "LOW",
                    "summary": "write FACTS.txt",
                },
            )
        reply = {
            "id": "stub",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [call],
                    },
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), Handler).serve_forever()
