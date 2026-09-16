"""Minimal OpenAI-compatible mock LLM used by ``timeout_restart_evidence.py``.

Two modes, toggled at runtime via ``POST /_control``:

``fast``
    answer immediately.
``hang``
    sleep ``hang_seconds`` before answering, so the *client's* configured
    request timeout is what decides when it gives up.

The hang mode is the whole point: an LLM request timeout is applied client
side, so the only way to observe which timeout the agent is really using is to
stall the server and measure when the client bails out.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


app = FastAPI()

STATE: dict[str, Any] = {
    "mode": "fast",
    "hang_seconds": 600.0,
    "requests": [],
}


@app.post("/_control")
async def control(request: Request) -> dict[str, Any]:
    """Switch modes; returns the new state (minus the request log)."""
    body = await request.json()
    STATE.update(body)
    return {"ok": True, "state": {k: v for k, v in STATE.items() if k != "requests"}}


@app.get("/_calls")
async def calls() -> dict[str, Any]:
    """Every completion request seen so far, oldest first."""
    return {"calls": STATE["requests"]}


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def completions(request: Request) -> JSONResponse:
    body = await request.json()
    started = time.time()
    STATE["requests"].append({"at": started, "mode": STATE["mode"]})
    if STATE["mode"] == "hang":
        await asyncio.sleep(float(STATE["hang_seconds"]))
    return JSONResponse(
        {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": int(started),
            "model": body.get("model", "mock"),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Hello from the mock LLM.",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }
    )
