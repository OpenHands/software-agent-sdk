"""Minimal OpenAI-compatible chat-completions server for agent-server benchmarks.

The last *user* message is a tiny script:

    SLEEP <seconds> [x<N>] [LAT <ms>] [TOKENS <n>]

- First LLM call after the user message: return N parallel `terminal` tool calls
  running `sleep <seconds>` (N defaults to 1).
- Any later call (tool results present): return a `finish` tool call.
- LAT adds artificial LLM latency per call (default 0).
- TOKENS makes the final answer a plain-text message of n words (no tools) so
  streaming/large-content paths can be exercised.
"""

import asyncio
import json
import re
import sys
import time
import uuid

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

CALLS = {"n": 0}


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def _plan(messages: list[dict]) -> tuple[str, dict]:
    last_user_idx = max(
        (i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1
    )
    script = _text(messages[last_user_idx]["content"]) if last_user_idx >= 0 else ""
    after = messages[last_user_idx + 1 :]
    tool_results = sum(1 for m in after if m.get("role") == "tool")
    m = re.search(r"SLEEP\s+([\d.]+)(?:\s+x(\d+))?", script)
    lat = re.search(r"LAT\s+(\d+)", script)
    toks = re.search(r"TOKENS\s+(\d+)", script)
    rate = re.search(r"RATE\s+(\d+)", script)
    opts = {
        "lat": int(lat.group(1)) / 1000 if lat else 0.0,
        "tokens": int(toks.group(1)) if toks else 0,
        "rate": int(rate.group(1)) if rate else 0,
    }
    if m and tool_results == 0:
        return "tools", {**opts, "sleep": m.group(1), "n": int(m.group(2) or 1)}
    loop = re.search(r"LOOP\s+(\d+)(?:\s+BYTES\s+(\d+))?", script)
    if loop and tool_results < int(loop.group(1)):
        return "loop", {**opts, "bytes": int(loop.group(2) or 200), "i": tool_results}
    return "finish", opts


def _tool_call(name: str, args: dict) -> dict:
    return {
        "id": f"call_{uuid.uuid4().hex[:12]}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


async def completions(request: Request):
    body = await request.json()
    CALLS["n"] += 1
    kind, opts = _plan(body.get("messages", []))
    if opts["lat"]:
        await asyncio.sleep(opts["lat"])
    if kind == "tools":
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                _tool_call(
                    "terminal",
                    {"command": f"sleep {opts['sleep']}", "security_risk": "LOW"},
                )
                for _ in range(opts["n"])
            ],
        }
    elif kind == "loop":
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [_tool_call("terminal", {
                "command": f"echo step-{opts['i']}; head -c {opts['bytes']} /dev/zero | tr '\\0' x; echo",
                "security_risk": "LOW",
            })],
        }
    elif opts["tokens"]:
        message = {
            "role": "assistant",
            "content": " ".join(f"w{i}" for i in range(opts["tokens"])),
        }
    else:
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [_tool_call("finish", {"message": "done"})],
        }
    resp = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "mock"),
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
    }
    if not body.get("stream"):
        return JSONResponse(resp)

    async def gen():
        # Stream content word-by-word; tool calls in one chunk.
        base = {k: resp[k] for k in ("id", "created", "model")}
        base["object"] = "chat.completion.chunk"
        if message.get("content"):
            for w in message["content"].split(" "):
                chunk = {**base, "choices": [{"index": 0, "delta": {"content": w + " "}}]}
                yield f"data: {json.dumps(chunk)}\n\n"
                if opts["rate"]:
                    await asyncio.sleep(1 / opts["rate"])
        if message.get("tool_calls"):
            tcs = [
                {**tc, "index": i} for i, tc in enumerate(message["tool_calls"])
            ]
            chunk = {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": tcs}}]}
            yield f"data: {json.dumps(chunk)}\n\n"
        end = {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": resp["usage"]}
        yield f"data: {json.dumps(end)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


async def stats(_: Request):
    return JSONResponse(CALLS)


app = Starlette(
    routes=[
        Route("/v1/chat/completions", completions, methods=["POST"]),
        Route("/chat/completions", completions, methods=["POST"]),
        Route("/stats", stats),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
