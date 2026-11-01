"""Stdio ACP fixture with gated shell writes and cooperative cancellation."""

import json
import subprocess
import sys
import threading
from pathlib import Path


root = Path(sys.argv[1])
output_lock = threading.Lock()
stop = threading.Event()
worker = None
turn = 0


def emit(message):
    with output_lock:
        print(json.dumps({"jsonrpc": "2.0", **message}), flush=True)


def respond(request_id, result):
    emit({"id": request_id, "result": result})


def update(session_id, content):
    emit(
        {
            "method": "session/update",
            "params": {
                "sessionId": session_id,
                "update": content,
            },
        }
    )


def finish(request_id, session_id, reason):
    update(session_id, {"sessionUpdate": "usage_update", "used": 0, "size": 100000})
    respond(request_id, {"stopReason": reason})


def wait_for_gate(name):
    while not (root / name).exists():
        if stop.wait(0.01):
            return False
    return not stop.is_set()


def run_prompt(request_id, session_id, turn):
    for step in range(1, 4):
        if not wait_for_gate(f"allow-{turn}-{step}"):
            finish(request_id, session_id, "cancelled")
            return
        call_id = f"{turn}-{step}"
        update(
            session_id,
            {
                "sessionUpdate": "tool_call",
                "toolCallId": call_id,
                "title": f"write step {call_id}",
                "kind": "execute",
                "status": "in_progress",
            },
        )
        subprocess.run(
            ["sh", "-c", f"printf '{call_id}\\n' >> steps.txt"], cwd=root, check=True
        )
        update(
            session_id,
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": call_id,
                "status": "completed",
            },
        )
    reason = "end_turn" if wait_for_gate(f"finish-{turn}") else "cancelled"
    finish(request_id, session_id, reason)


try:
    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params", {})
        if method == "initialize":
            respond(
                request_id,
                {
                    "protocolVersion": 1,
                    "agentCapabilities": {"loadSession": True},
                    "agentInfo": {"name": "pause-fixture", "version": "1"},
                },
            )
        elif method == "session/new":
            respond(request_id, {"sessionId": "pause-session"})
        elif method == "session/load":
            respond(request_id, {})
        elif method == "session/prompt":
            turn += 1
            with (root / "prompts.jsonl").open("a") as stream:
                stream.write(json.dumps(params["prompt"]) + "\n")
            stop.clear()
            worker = threading.Thread(
                target=run_prompt, args=(request_id, params["sessionId"], turn)
            )
            worker.start()
        elif method == "session/cancel":
            with (root / "cancels.txt").open("a") as stream:
                stream.write("session/cancel\n")
            stop.set()
        elif request_id is not None:
            respond(request_id, {})
finally:
    stop.set()
    if worker is not None:
        worker.join(timeout=2)
