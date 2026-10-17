"""A local OpenAI-compatible endpoint for programs that drive a real `LLM`.

`TestLLM` bypasses LiteLLM; a program that must prove the SDK's own provider
path (request building, streaming, retries, error mapping, metrics, logs)
points a real `LLM(model="openai/...", base_url=server.url)` at this server
instead. It answers `/v1/chat/completions`, `/v1/responses` and Anthropic's
`/v1/messages` (pass `base_url=server.root`) from a script,
one step per HTTP request, and records every request for the program to report.

    from S02.fake_provider import FakeProvider

    with FakeProvider([{"text": "hi"}, {"status": 429}]) as server:
        llm = LLM(model="openai/gpt-4o-mini", base_url=server.url, api_key="k")
        ...
        server.requests  # [{"path": ..., "body": {...}, "headers": {...}}]

Steps (one per request):
  {"text": "...", "reasoning": "...", "tool": NAME, "args": {...}}
  {"tools": [{"tool": NAME, "args": {...}}, ...]}   parallel tool calls
  {"finish": "message"}                             the finish tool
  {"status": 429, "message": "..."}                 an HTTP error
  {"empty": true}                                   a reply with no choices
  {"delay": 2.0, ...}                               wait before answering
  {"stall": 2.0, ...}                               (stream) pause after two events
  {"thinking": "...", "redacted": "..."}           Anthropic thinking blocks
  {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "cached": 0}}
  {"encrypted": "..."}                              Responses: encrypted reasoning
  {"incomplete": true}                              Responses stream: no completed event
A request whose body has `"stream": true` is answered as server-sent events.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


# A 1x1 PNG, served at /image.png for image-input programs.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _calls(step: dict[str, Any]) -> list[dict[str, Any]]:
    if "finish" in step:
        return [{"tool": "finish", "args": {"message": step["finish"]}}]
    if "tool" in step:
        return [step]
    return list(step.get("tools", []))


def _error_type(status: int) -> str:
    """The `type` OpenAI puts on an error body; LiteLLM maps on it."""
    if status == 429:
        return "rate_limit_exceeded"
    if status >= 500:
        return "server_error"
    return "invalid_request_error"


def _usage(step: dict[str, Any]) -> dict[str, int]:
    usage = {"prompt_tokens": 100, "completion_tokens": 20, "cached": 0}
    usage.update(step.get("usage", {}))
    return usage


class FakeProvider:
    def __init__(self, script: list[dict[str, Any]], model: str = "gpt-4o-mini"):
        self.script = list(script)
        self.model = model
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        provider = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                if self.path.startswith("/image.png"):
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(PNG)))
                    self.end_headers()
                    self.wfile.write(PNG)
                    return
                self._send(404, {"error": {"message": "not found"}})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    body = {"_raw": raw.decode(errors="replace")}
                with provider.lock:
                    provider.requests.append(
                        {
                            "path": self.path,
                            "body": body,
                            "headers": {k.lower(): v for k, v in self.headers.items()},
                        }
                    )
                    step = provider.script.pop(0) if provider.script else None
                if step is None:
                    self._send(
                        500, {"error": {"message": "fake provider: script exhausted"}}
                    )
                    return
                if step.get("delay"):
                    time.sleep(float(step["delay"]))
                if "status" in step:
                    self._send(
                        int(step["status"]),
                        {
                            "error": {
                                "message": step.get("message", "scripted error"),
                                "type": step.get(
                                    "type", _error_type(int(step["status"]))
                                ),
                                "code": step.get("code"),
                            }
                        },
                    )
                    return
                path = self.path.split("?")[0].rstrip("/")
                if path.endswith("/messages"):
                    self._send(200, provider.anthropic_reply(step))
                    return
                responses = path.endswith("/responses")
                if body.get("stream"):
                    events = (
                        provider.responses_stream(step)
                        if responses
                        else provider.chat_stream(step)
                    )
                    self._stream(events, step)
                    return
                payload = (
                    provider.responses_reply(step)
                    if responses
                    else provider.chat_reply(step)
                )
                self._send(200, payload)

            def _send(self, code: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _stream(
                self,
                events: list[tuple[str | None, dict[str, Any]]],
                step: dict[str, Any],
            ) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                for k, (name, event) in enumerate(events):
                    if k == 2 and step.get("stall"):
                        time.sleep(float(step["stall"]))
                    line = (f"event: {name}\n" if name else "") + (
                        f"data: {json.dumps(event)}\n\n"
                    )
                    self.wfile.write(line.encode())
                    self.wfile.flush()
                    time.sleep(0.01)
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.root = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.url = self.root + "/v1"
        self.image_url = self.root + "/image.png"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> FakeProvider:
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()

    # ------------------------------------------------------------------
    # Chat Completions
    # ------------------------------------------------------------------

    def chat_reply(self, step: dict[str, Any]) -> dict[str, Any]:
        usage = _usage(step)
        base = {
            "id": f"chatcmpl-{len(self.requests)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.model,
            "usage": {
                "prompt_tokens": usage["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"],
                "total_tokens": usage["prompt_tokens"] + usage["completion_tokens"],
                "prompt_tokens_details": {"cached_tokens": usage["cached"]},
            },
        }
        if step.get("empty"):
            return {**base, "choices": []}
        calls = _calls(step)
        message: dict[str, Any] = {"role": "assistant", "content": step.get("text", "")}
        if step.get("reasoning"):
            message["reasoning_content"] = step["reasoning"]
        if calls:
            message["tool_calls"] = [
                {
                    "id": f"call_{len(self.requests)}_{i}",
                    "type": "function",
                    "function": {
                        "name": c["tool"],
                        "arguments": json.dumps(c.get("args", {})),
                    },
                }
                for i, c in enumerate(calls)
            ]
        return {
            **base,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if calls else "stop",
                }
            ],
        }

    def chat_stream(
        self, step: dict[str, Any]
    ) -> list[tuple[str | None, dict[str, Any]]]:
        usage = _usage(step)
        n = len(self.requests)

        def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
            return {
                "id": f"chatcmpl-{n}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": self.model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }

        out: list[tuple[str | None, dict[str, Any]]] = [
            (None, chunk({"role": "assistant", "content": ""}))
        ]
        reasoning = step.get("reasoning", "")
        for i in range(0, len(reasoning), 4):
            out.append((None, chunk({"reasoning_content": reasoning[i : i + 4]})))
        text = step.get("text", "")
        for i in range(0, len(text), 4):
            out.append((None, chunk({"content": text[i : i + 4]})))
        calls = _calls(step)
        for k, call in enumerate(calls):
            args = json.dumps(call.get("args", {}))
            out.append(
                (
                    None,
                    chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": k,
                                    "id": f"call_{n}_{k}",
                                    "type": "function",
                                    "function": {"name": call["tool"], "arguments": ""},
                                }
                            ]
                        }
                    ),
                )
            )
            for i in range(0, len(args), 8):
                out.append(
                    (
                        None,
                        chunk(
                            {
                                "tool_calls": [
                                    {
                                        "index": k,
                                        "function": {"arguments": args[i : i + 8]},
                                    }
                                ]
                            }
                        ),
                    )
                )
        last = chunk({}, "tool_calls" if calls else "stop")
        last["usage"] = {
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "total_tokens": usage["prompt_tokens"] + usage["completion_tokens"],
            "prompt_tokens_details": {"cached_tokens": usage["cached"]},
        }
        out.append((None, last))
        return out

    # ------------------------------------------------------------------
    # Anthropic Messages (base_url=server.root)
    # ------------------------------------------------------------------

    def anthropic_reply(self, step: dict[str, Any]) -> dict[str, Any]:
        usage = _usage(step)
        n = len(self.requests)
        content: list[dict[str, Any]] = []
        if step.get("thinking"):
            content.append(
                {
                    "type": "thinking",
                    "thinking": step["thinking"],
                    "signature": f"sig-{n}",
                }
            )
        if step.get("redacted"):
            content.append({"type": "redacted_thinking", "data": step["redacted"]})
        if step.get("text"):
            content.append({"type": "text", "text": step["text"]})
        calls = _calls(step)
        for i, call in enumerate(calls):
            content.append(
                {
                    "type": "tool_use",
                    "id": f"toolu_{n}_{i}",
                    "name": call["tool"],
                    "input": call.get("args", {}),
                }
            )
        return {
            "id": f"msg_{n}",
            "type": "message",
            "role": "assistant",
            "model": self.model,
            "content": content,
            "stop_reason": "tool_use" if calls else "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": usage["prompt_tokens"],
                "output_tokens": usage["completion_tokens"],
                "cache_read_input_tokens": usage["cached"],
                "cache_creation_input_tokens": 0,
            },
        }

    # ------------------------------------------------------------------
    # Responses API
    # ------------------------------------------------------------------

    def _responses_output(self, step: dict[str, Any]) -> list[dict[str, Any]]:
        n = len(self.requests)
        output: list[dict[str, Any]] = []
        if step.get("reasoning") or step.get("encrypted"):
            item: dict[str, Any] = {
                "type": "reasoning",
                "id": f"rs_{n}",
                "summary": [
                    {"type": "summary_text", "text": step.get("reasoning", "")}
                ],
            }
            if step.get("encrypted"):
                item["encrypted_content"] = step["encrypted"]
            output.append(item)
        if step.get("text"):
            output.append(
                {
                    "type": "message",
                    "id": f"msg_{n}",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": step["text"], "annotations": []}
                    ],
                }
            )
        for i, call in enumerate(_calls(step)):
            output.append(
                {
                    "type": "function_call",
                    "id": f"fc_{n}_{i}",
                    "call_id": f"call_{n}_{i}",
                    "name": call["tool"],
                    "arguments": json.dumps(call.get("args", {})),
                    "status": "completed",
                }
            )
        return output

    def responses_reply(self, step: dict[str, Any]) -> dict[str, Any]:
        usage = _usage(step)
        return {
            "id": f"resp_{len(self.requests)}",
            "object": "response",
            "created_at": int(time.time()),
            "status": "completed",
            "model": self.model,
            "output": [] if step.get("empty") else self._responses_output(step),
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "usage": {
                "input_tokens": usage["prompt_tokens"],
                "output_tokens": usage["completion_tokens"],
                "total_tokens": usage["prompt_tokens"] + usage["completion_tokens"],
                "input_tokens_details": {"cached_tokens": usage["cached"]},
                "output_tokens_details": {
                    "reasoning_tokens": 7 if step.get("reasoning") else 0
                },
            },
        }

    def responses_stream(
        self, step: dict[str, Any]
    ) -> list[tuple[str | None, dict[str, Any]]]:
        final = self.responses_reply(step)
        created = {**final, "status": "in_progress", "output": []}
        out: list[tuple[str | None, dict[str, Any]]] = [
            ("response.created", {"type": "response.created", "response": created})
        ]
        for index, item in enumerate(final["output"]):
            out.append(
                (
                    "response.output_item.added",
                    {
                        "type": "response.output_item.added",
                        "output_index": index,
                        "item": item,
                    },
                )
            )
            if item["type"] == "message":
                text = item["content"][0]["text"]
                for i in range(0, len(text), 4):
                    out.append(
                        (
                            "response.output_text.delta",
                            {
                                "type": "response.output_text.delta",
                                "item_id": item["id"],
                                "output_index": index,
                                "content_index": 0,
                                "delta": text[i : i + 4],
                            },
                        )
                    )
            out.append(
                (
                    "response.output_item.done",
                    {
                        "type": "response.output_item.done",
                        "output_index": index,
                        "item": item,
                    },
                )
            )
        if not step.get("incomplete"):
            out.append(
                (
                    "response.completed",
                    {"type": "response.completed", "response": final},
                )
            )
        return out
