"""Minimal stdio ACP server that reports models, for model-discovery tests.

``FAKE_ACP_MODELS`` picks how models are reported: ``config`` (a ``model``
configOptions select) or ``legacy`` (the UNSTABLE ``models`` block). A
``FAKE_ACP_KEY`` in the environment unlocks a ``premium`` model; with
``FAKE_ACP_REQUIRE_KEY`` set, ``session/new`` fails without it. With
``FAKE_ACP_EFFORT`` set, ``m1`` also offers a ``thought_level`` select (id
``thinking``) that a model switch resets; ``m2`` offers no effort.
"""

import json
import os
import sys
from typing import Any


MODELS = ["m1", "m2"] + (["premium"] if os.environ.get("FAKE_ACP_KEY") else [])
EFFORTS = {"m1": ["low", "high"]}
DEFAULT_EFFORT = "high"
session = {"model": "m1", "effort": DEFAULT_EFFORT}


def respond(request_id, result=None, error=None):
    message = {"jsonrpc": "2.0", "id": request_id}
    if error is None:
        message["result"] = result if result is not None else {}
    else:
        message["error"] = error
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def model_select(current: str) -> dict[str, Any]:
    return {
        "id": "model",
        "name": "Model",
        "type": "select",
        "currentValue": current,
        "options": [{"value": m, "name": m.upper()} for m in MODELS],
    }


def config_options() -> list[dict[str, Any]]:
    options = [model_select(session["model"])]
    levels = EFFORTS.get(session["model"])
    if os.environ.get("FAKE_ACP_EFFORT") and levels:
        options.append(
            {
                "id": "thinking",
                "name": "Thinking",
                "type": "select",
                "category": "thought_level",
                "currentValue": session["effort"],
                "options": [{"value": e, "name": e.title()} for e in levels],
            }
        )
    return options


def set_config_option(params: dict[str, Any]):
    config_id, value = params.get("configId"), params.get("value")
    if config_id == "model" and value in MODELS:
        session.update(model=value, effort=DEFAULT_EFFORT)
    elif config_id == "thinking" and value in EFFORTS.get(session["model"], []):
        session["effort"] = value
    else:
        return None, {"code": -32602, "message": f"Unknown {config_id} {value}"}
    return {"configOptions": config_options()}, None


def new_session():
    if os.environ.get("FAKE_ACP_REQUIRE_KEY") and not os.environ.get("FAKE_ACP_KEY"):
        return None, {"code": -32000, "message": "Authentication required"}
    result: dict[str, Any] = {"sessionId": "fake-session"}
    if os.environ.get("FAKE_ACP_MODELS") == "legacy":
        result["models"] = {
            "currentModelId": "m1",
            "availableModels": [
                {"modelId": m, "name": m.upper(), "description": f"{m} model"}
                for m in MODELS
            ],
        }
    else:
        result["configOptions"] = config_options()
    return result, None


def main():
    for line in sys.stdin:
        message = json.loads(line)
        request_id = message.get("id")
        if request_id is None:
            continue
        method = message["method"]
        params = message.get("params") or {}
        if method == "initialize":
            respond(
                request_id,
                {
                    "protocolVersion": 1,
                    "agentInfo": {"name": "fake-acp", "version": "1.2.3"},
                },
            )
        elif method == "session/new":
            result, error = new_session()
            respond(request_id, result, error)
        elif method == "session/set_model":
            if params.get("modelId") not in MODELS:
                respond(request_id, error={"code": -32602, "message": "Unknown model"})
            else:
                respond(request_id)
        elif method == "session/set_config_option":
            result, error = set_config_option(params)
            respond(request_id, result, error)
        else:
            respond(request_id, error={"code": -32601, "message": "Method not found"})


if __name__ == "__main__":
    main()
