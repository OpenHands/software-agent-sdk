"""Deterministic ACP permission peer; never executes the proposed operation."""

import json
import os
import sys
import uuid
from pathlib import Path


output = Path(sys.argv[1])
session_id = "permission-test-" + uuid.uuid4().hex
record = {
    "session_id": session_id,
    "mode_requests": [],
    "calls": [],
    "environment_observation": {
        "allowed_env": os.environ.get("PROOF_ALLOWED_ENV") == "selected",
        "control_env": "PROOF_CONTROL_KEY" in os.environ,
        "allowed_secret": os.environ.get("PROOF_ALLOWED_SECRET") == "approved",
        "forbidden_secret": "PROOF_FORBIDDEN_SECRET" in os.environ,
        "file_path": bool(os.environ.get("PROOF_FILE_PATH"))
        and Path(os.environ["PROOF_FILE_PATH"]).is_file(),
    },
}


def save():
    output.write_text(json.dumps(record), encoding="utf-8")


def send(message):
    print(json.dumps({"jsonrpc": "2.0", **message}), flush=True)


prompt_id = None
for line in sys.stdin:
    message = json.loads(line)
    method, request_id = message.get("method"), message.get("id")
    record["calls"].append(method)
    save()
    if method == "initialize":
        send(
            {
                "id": request_id,
                "result": {
                    "protocolVersion": 1,
                    "agentCapabilities": {
                        "loadSession": "--load-supported" in sys.argv
                    },
                    "authMethods": [],
                    "agentInfo": {"name": "codex-acp", "version": "fixture"},
                },
            }
        )
    elif method == "session/new":
        save()
        send({"id": request_id, "result": {"sessionId": session_id}})
    elif method == "session/set_mode":
        record["mode_requests"].append(message["params"]["modeId"])
        save()
        send({"id": request_id, "result": {}})
    elif method == "session/prompt":
        prompt_id = request_id
        partial = "--partial-permission" in sys.argv
        if partial:
            send(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "unrelated-session"
                        if "--cross-session-update" in sys.argv
                        else session_id,
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": "simulated",
                            "title": "Simulated operation",
                            "status": "pending",
                            "kind": "execute",
                            "rawInput": {"command": "simulated-approved"},
                        },
                    },
                }
            )
        send(
            {
                "id": "permission-request",
                "method": "session/request_permission",
                "params": {
                    "sessionId": session_id,
                    "toolCall": {
                        "toolCallId": "simulated",
                        **({} if partial else {"title": "Simulated operation"}),
                        "status": "pending",
                        "kind": "execute",
                    },
                    "options": [
                        {
                            "optionId": "allow-id",
                            "name": "Allow once",
                            "kind": "allow_once",
                        },
                        {
                            "optionId": "deny-id",
                            "name": "Deny once",
                            "kind": "reject_once",
                        },
                    ],
                },
            }
        )
    elif request_id == "permission-request" and method is None:
        assert prompt_id is not None
        record["response"] = message.get("result")
        save()
        send({"id": prompt_id, "result": {"stopReason": "end_turn"}})
    elif method and request_id is not None:
        send(
            {
                "id": request_id,
                "error": {"code": -32601, "message": "Unsupported fixture method"},
            }
        )
