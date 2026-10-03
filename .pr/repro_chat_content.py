"""Exercise #4965 through SDK -> LiteLLM -> a real local HTTP server.

From the SDK checkout, after `make build`, run:
    .venv/bin/python .pr/repro_chat_content.py

Exit 1 means malformed HTTP requests were reproduced; exit 0 means all cases
were accepted. No SDK or LiteLLM transport/serialization methods are mocked.
"""

import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any


# Do this before importing libraries: ignore inherited credentials and .env files.
os.environ.clear()
os.environ.update(
    {
        "LITELLM_MODE": "PRODUCTION",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "PYTHON_DOTENV_DISABLED": "1",
        "OPENHANDS_SUPPRESS_BANNER": "1",
        "LOG_LEVEL": "CRITICAL",
        "DO_NOT_TRACK": "1",
        "NO_PROXY": "*",
    }
)


def allow_loopback_only(event: str, args: tuple[Any, ...]) -> None:
    if event == "socket.connect" and args[1][0] != "127.0.0.1":
        raise RuntimeError("This reproduction only permits loopback connections")


sys.addaudithook(allow_loopback_only)

from openhands.sdk import LLM  # noqa: E402
from openhands.sdk.llm import Message, MessageToolCall, TextContent  # noqa: E402


logging.disable(logging.CRITICAL)
received: list[dict[str, Any]] = []


class StrictChatServer(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:
        assert self.path == "/v1/chat/completions", self.path
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        errors = []
        for index, message in enumerate(body["messages"]):
            content = message.get("content")
            label = f"messages[{index}] ({message['role']})"
            if content == []:
                errors.append(f"{label}: content=[]")
            elif isinstance(content, list):
                for block in content:
                    if block.get("type") == "text" and not block["text"].strip():
                        errors.append(f"{label}: blank text block")
        status = 400 if errors else 200
        received.append({"status": status, "errors": errors, "body": body})
        payload = (
            {
                "error": {
                    "message": "; ".join(errors),
                    "type": "invalid_request_error",
                    "param": "messages",
                    "code": "invalid_value",
                }
            }
            if errors
            else {
                "id": "chatcmpl-local-reproduction",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "local-ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
        )
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    prompt = Message(role="user", content=[TextContent(text="Continue.")])
    tool_call = Message(
        role="assistant",
        content=[],
        tool_calls=[
            MessageToolCall(
                id="call-local",
                name="local_noop",
                arguments="{}",
                origin="completion",
            )
        ],
    )
    cases = {
        "empty user": [Message(role="user", content=[])],
        "blank assistant": [
            prompt,
            Message(role="assistant", content=[TextContent(text=" \n\t")]),
            prompt,
        ],
        "mixed blank/valid user blocks": [
            Message(
                role="user",
                content=[TextContent(text="  "), TextContent(text="Continue.")],
            )
        ],
        "empty tool result": [
            prompt,
            tool_call,
            Message(
                role="tool", content=[], tool_call_id="call-local", name="local_noop"
            ),
            prompt,
        ],
    }
    failures = 0
    with ThreadingHTTPServer(("127.0.0.1", 0), StrictChatServer) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            llm = LLM(
                model="openai/gpt-4o",
                api_key="local-test-only-not-a-real-key",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                num_retries=0,
                timeout=5,
                max_output_tokens=16,
                caching_prompt=False,
                log_completions=False,
            )
            for name, messages in cases.items():
                before = len(received)
                original = [message.model_dump() for message in messages]
                try:
                    result = llm.completion(messages)
                except Exception as error:
                    if len(received) == before or received[-1]["status"] != 400:
                        raise RuntimeError(
                            f"{name}: unexpected transport failure"
                        ) from error
                    failures += 1
                    print(f"FAIL {name}: HTTP 400; {'; '.join(received[-1]['errors'])}")
                else:
                    assert isinstance(result.message.content[0], TextContent)
                    assert result.message.content[0].text == "local-ok"
                    assert len(received) > before and received[-1]["status"] == 200
                    print(f"PASS {name}: HTTP 200; SDK response=local-ok")
                assert original == [message.model_dump() for message in messages]
        finally:
            server.shutdown()
            worker.join(timeout=5)
    print(f"{len(cases) - failures}/{len(cases)} cases passed; loopback HTTP only")
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
