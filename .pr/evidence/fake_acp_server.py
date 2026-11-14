"""Fake ACP server with per-session memory, for software-agent-sdk#5094.

usage: python fake_acp.py <state_dir>
Touch <state_dir>/fail-load to make session/load fail with -32603 Internal error.
"""

import json
import sys
from pathlib import Path


root = Path(sys.argv[1])
(root / "sessions").mkdir(parents=True, exist_ok=True)


def send(obj):
    print(json.dumps(obj), flush=True)


def log(obj):
    with (root / "calls.jsonl").open("a") as f:
        f.write(json.dumps(obj) + "\n")


def user_text(prompt):
    texts = [b.get("text", "") for b in prompt if b.get("type") == "text"]
    return texts[-1].strip().splitlines()[-1] if texts else ""


for line in sys.stdin:
    req = json.loads(line)
    method = req.get("method")
    log({"method": method, "params": req.get("params", {}).get("sessionId")})
    if "id" not in req:
        continue
    result = {}
    if method == "initialize":
        result = {
            "protocolVersion": 1,
            "agentCapabilities": {"loadSession": True},
            "authMethods": [],
            "agentInfo": {"name": "fake-acp", "version": "1.0"},
        }
    elif method == "session/new":
        n = len(list((root / "sessions").iterdir())) + 1
        sid = f"sess-{n}"
        (root / "sessions" / f"{sid}.json").write_text("[]")
        result = {"sessionId": sid}
    elif method == "session/load":
        if (root / "fail-load").exists():
            send(
                {
                    "jsonrpc": "2.0",
                    "id": req["id"],
                    "error": {
                        "code": -32603,
                        "message": "Internal error",
                        "data": {"reason": "session store locked by another process"},
                    },
                }
            )
            continue
    elif method == "session/prompt":
        sid = req["params"]["sessionId"]
        path = root / "sessions" / f"{sid}.json"
        memory = json.loads(path.read_text()) if path.exists() else []
        text = user_text(req["params"].get("prompt", []))
        if memory:
            reply = (
                f"[ACP session {sid}] I remember {len(memory)} earlier message(s): "
                + "; ".join(f'"{m}"' for m in memory)
            )
        else:
            reply = (
                f"[ACP session {sid}] I have no earlier context in this session. "
                "What were we discussing?"
            )
        memory.append(text)
        path.write_text(json.dumps(memory))
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": sid,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": reply},
                    },
                },
            }
        )
        result = {"stopReason": "end_turn"}
    send({"jsonrpc": "2.0", "id": req["id"], "result": result})
