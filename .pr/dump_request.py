"""Dump the first LLM request a GAIA-like agent sends (stub LLM, real tools).

uv run python .pr/dump_request.py <out.json> [<inline system prompt>]
"""

import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.tools.preset.default import get_default_tools


captured: list[dict] = []


class Stub(BaseHTTPRequestHandler):
    def do_POST(self):
        captured.append(
            json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        )
        call = {
            "id": "c1",
            "type": "function",
            "function": {"name": "finish", "arguments": json.dumps({"message": "ok"})},
        }
        reply = {
            "id": "s",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
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

    def log_message(self, *a):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
threading.Thread(target=server.serve_forever, daemon=True).start()
system_prompt = sys.argv[2] if len(sys.argv) > 2 else None
llm = LLM(
    model="openai/gpt-4o",
    base_url=f"http://127.0.0.1:{server.server_port}/v1",
    api_key=SecretStr("stub"),
    usage_id="dump",
)
agent = Agent(
    llm=llm,
    tools=get_default_tools(enable_browser=True),
    system_prompt_kwargs={
        "cli_mode": True,
        "soul_content": (
            "You are OpenHands agent, a helpful AI assistant that can interact"
            " with a computer to solve tasks."
        ),
    },
    system_prompt=system_prompt,
)
with tempfile.TemporaryDirectory() as ws:
    conv = Conversation(agent=agent, workspace=ws)
    conv.send_message("hi")
    conv.run()
    conv.close()
server.shutdown()
req = captured[0]
msg0 = req["messages"][0]["content"]
system = msg0 if isinstance(msg0, str) else "\n".join(b.get("text", "") for b in msg0)
out = {
    "system": system,
    "tools": {
        t["function"]["name"]: t["function"]["description"] for t in req["tools"]
    },
    "tool_params": {
        t["function"]["name"]: t["function"]["parameters"] for t in req["tools"]
    },
}
json.dump(out, open(sys.argv[1], "w"), indent=1, ensure_ascii=False)
print("tools:", len(out["tools"]), "system chars:", len(system))
