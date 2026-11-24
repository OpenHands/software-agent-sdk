"""Minimal stdio ACP server that stalls on one request, for startup-retry tests.

``FAKE_ACP_STALL_METHOD`` names the request to leave unanswered. It stalls on
the first launch only (tracked by the ``FAKE_ACP_MARKER`` file), or on every
launch when ``FAKE_ACP_STALL_ALWAYS`` is set. Each request is appended to
``FAKE_ACP_LOG`` as ``<launch number> <method>``.
"""

import json
import os
import sys
import time


marker = os.environ["FAKE_ACP_MARKER"]
log_path = os.environ["FAKE_ACP_LOG"]
stall_method = os.environ["FAKE_ACP_STALL_METHOD"]

with open(marker, "a+") as f:
    f.write("x")
    f.seek(0)
    launch = len(f.read())
stalls = launch == 1 or bool(os.environ.get("FAKE_ACP_STALL_ALWAYS"))


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method, request_id = message["method"], message["id"]
    with open(log_path, "a") as f:
        f.write(f"{launch} {method}\n")
    if stalls and method == stall_method:
        time.sleep(3600)
    if method == "initialize":
        result = {
            "protocolVersion": 1,
            "agentInfo": {"name": "fake-stalling-acp", "version": "0.0.1"},
        }
    elif method == "session/new":
        result = {"sessionId": "fake-session"}
    elif method == "session/prompt":
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "fake-session",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "hello"},
                    },
                },
            }
        )
        result = {"stopReason": "end_turn"}
    else:
        result = {}
    send({"jsonrpc": "2.0", "id": request_id, "result": result})
