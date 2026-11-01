"""Public Conversation -> real LiteLLM transport -> local HTTP endpoint.

This captures the first network hop only. The endpoint does not run a LiteLLM
proxy and cannot establish OpenRouter forwarding or cache savings.
"""

import argparse
import asyncio
import importlib.metadata
import json
import subprocess
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.llm import Message, TextContent


parser = argparse.ArgumentParser()
parser.add_argument("--enable-body", action="store_true")
parser.add_argument("--asynchronous", action="store_true")
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
root = Path.cwd()
seen = []
guard = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with guard:
            seen.append(
                {
                    "path": self.path,
                    "header_session_id": self.headers.get("x-litellm-session-id"),
                    "body_session_id": body.get("session_id"),
                    "prompt_cache_key": body.get("prompt_cache_key"),
                }
            )
        payload = json.dumps(
            {
                "id": "chatcmpl-" + uuid4().hex,
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_" + uuid4().hex,
                                    "type": "function",
                                    "function": {
                                        "name": "finish",
                                        "arguments": json.dumps({"message": "done"}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
conversations = []
try:
    options = {"send_session_id_in_body": True} if args.enable_body else {}
    llm = LLM(
        model="litellm_proxy/gpt-4o-mini",
        api_key=SecretStr("local-endpoint-test-key"),
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        stream=False,
        usage_id="proxy-session-reproduction",
        **options,
    )
    agent = Agent(llm=llm, tools=[])
    first = Conversation(agent=agent, visualizer=None)
    second = Conversation(agent=agent, visualizer=None)
    conversations.extend([first, second])
    first.send_message("first")
    second.send_message("second")
    messages = [
        Message(role="system", content=[TextContent(text="Answer briefly.")]),
        Message(role="user", content=[TextContent(text="direct")]),
    ]
    if args.asynchronous:

        async def run():
            await llm.acompletion(messages)
            await asyncio.gather(first.arun(), second.arun())
            first.send_message("another turn")
            await first.arun()

        asyncio.run(run())
    else:
        llm.completion(messages)
        first.run()
        second.run()
        first.send_message("another turn")
        first.run()
    expected = Counter(
        {(str(first.id), str(first.id)): 2, (str(second.id), str(second.id)): 1}
    )
    observed = Counter((x["header_session_id"], x["body_session_id"]) for x in seen[1:])
    passed = (
        seen[0]["header_session_id"] is None
        and seen[0]["body_session_id"] is None
        and observed == expected
    )
    report = {
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "sdk_version": importlib.metadata.version("openhands-sdk"),
        "litellm_version": importlib.metadata.version("litellm"),
        "asynchronous": args.asynchronous,
        "enable_body": args.enable_body,
        "conversation_ids": {"first": str(first.id), "second": str(second.id)},
        "requests": seen,
        "conversation_body_id_contract_passed": passed,
        "scope": (
            "SDK to local HTTP endpoint; "
            "no real proxy or OpenRouter/cache validation"
        ),
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    assert passed, "conversation ID missing or isolated incorrectly in HTTP body"
finally:
    for conversation in conversations:
        conversation.close()
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
