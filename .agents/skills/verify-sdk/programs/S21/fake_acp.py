"""A stand-in ACP server: a stdio JSON-RPC process that behaves like a coding
agent CLI (claude-agent-acp, codex-acp) closely enough for an `ACPAgent` to
drive it, and journals everything it is asked so a program can inspect it.

It is launched by the SDK, never directly:

    ACPAgent(acp_command=[sys.executable, ".../S21/fake_acp.py"])

Environment (inherited from the program through the SDK):
  FAKE_ACP_JOURNAL        JSON-lines file every request is appended to
  FAKE_ACP_SESSIONS       file of session ids this "server" remembers across
                          restarts (what session/load can resume)
  FAKE_ACP_NAME           agentInfo.name to report (default fake-acp); e.g.
                          codex-acp makes the SDK treat it as Codex
  FAKE_ACP_AUTH_METHODS   comma-separated auth method ids to advertise
  FAKE_ACP_REQUIRE_KEY    session/new fails "Authentication required" unless
                          FAKE_ACP_KEY is set
  FAKE_ACP_INIT_SLEEP     seconds to stall in initialize
  FAKE_ACP_LEGACY_MODELS  report models in the UNSTABLE `models` block

What a prompt does depends on a tag in its first text block:
  (none)     thought, two tool calls (the first asks permission and streams
             progress), a two-chunk reply, usage_update with cost, usage
  [secret]   as above, echoing $FAKE_TOKEN in the reply, thought and tool output
  [big]      a tool call whose output is 40,000 characters
  [silent]   no reply text at all
  [slow]     4 s of thought chunks every 0.5 s, then the normal turn
  [hang]     no activity until cancelled
  [flaky]    the first [flaky] prompt of a session fails with -32603
             (internal error); the retry succeeds
  [fail]     fails with -32602 (not retriable, not an auth failure)
  [cancel]   starts a tool call and waits for session/cancel
A forked session (ask_agent) answers "Fork answer: <question>".
"""

import itertools
import json
import os
import sys
import threading
import time
import uuid


JOURNAL = os.environ.get("FAKE_ACP_JOURNAL")
SESSIONS = os.environ.get("FAKE_ACP_SESSIONS")
NAME = os.environ.get("FAKE_ACP_NAME", "fake-acp")
MODELS = ["m1", "m2"] + (["premium"] if os.environ.get("FAKE_ACP_KEY") else [])
if NAME.startswith("codex"):
    MODELS += ["gpt-5.5"]

out_lock = threading.Lock()
journal_lock = threading.Lock()
pending: dict[int, list] = {}
ids = itertools.count(1000)
cancelled: set[str] = set()
models: dict[str, str] = {}
costs: dict[str, float] = {}
attempts: dict[str, int] = {}
forks: set[str] = set()
seen_tags: set[tuple[str, str]] = set()


def journal(kind: str, **data) -> None:
    if not JOURNAL:
        return
    with journal_lock, open(JOURNAL, "a") as f:
        f.write(
            json.dumps({"kind": kind, "pid": os.getpid(), "t": time.time(), **data})
            + "\n"
        )


def write(message: dict) -> None:
    with out_lock:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def respond(request_id, result=None, error=None) -> None:
    message = {"jsonrpc": "2.0", "id": request_id}
    if error is None:
        message["result"] = result if result is not None else {}
    else:
        message["error"] = error
    write(message)


def notify(session_id: str, update: dict) -> None:
    write(
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": session_id, "update": update},
        }
    )


def call_client(method: str, params: dict, timeout: float = 10.0):
    request_id = next(ids)
    event = threading.Event()
    pending[request_id] = [event, None]
    write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    event.wait(timeout)
    return pending.pop(request_id)[1]


def text(t: str) -> dict:
    return {"type": "text", "text": t}


def known_sessions() -> set[str]:
    if not SESSIONS or not os.path.exists(SESSIONS):
        return set()
    return set(open(SESSIONS).read().split())


def remember(session_id: str) -> None:
    if SESSIONS:
        with open(SESSIONS, "a") as f:
            f.write(session_id + "\n")


def model_options(session_id: str) -> list[dict]:
    options = [
        {
            "id": "model",
            "name": "Model",
            "type": "select",
            "currentValue": models.get(session_id, "m1"),
            "options": [{"value": m, "name": m.upper()} for m in MODELS],
        }
    ]
    if NAME.startswith("codex"):
        options.append(
            {
                "id": "reasoning_effort",
                "name": "Effort",
                "type": "select",
                "currentValue": "medium",
                "options": [{"value": v, "name": v} for v in ("low", "medium", "high")],
            }
        )
    return options


def session_models(session_id: str) -> dict:
    if os.environ.get("FAKE_ACP_LEGACY_MODELS"):
        return {
            "models": {
                "currentModelId": models.get(session_id, "m1"),
                "availableModels": [{"modelId": m, "name": m.upper()} for m in MODELS],
            }
        }
    return {"configOptions": model_options(session_id)}


def finish_usage(session_id: str, cost: float = 0.25) -> dict:
    costs[session_id] = costs.get(session_id, 0.0) + cost
    notify(
        session_id,
        {
            "sessionUpdate": "usage_update",
            "used": 1200,
            "size": 200000,
            "cost": {"amount": round(costs[session_id], 6), "currency": "USD"},
        },
    )
    return {
        "stopReason": "end_turn",
        "usage": {
            "totalTokens": 160,
            "inputTokens": 100,
            "outputTokens": 50,
            "cachedReadTokens": 10,
            "thoughtTokens": 0,
        },
    }


def normal_turn(session_id: str, n: int, tag: str) -> dict:
    secret = os.environ.get("FAKE_TOKEN", "") if tag == "[secret]" else ""
    notify(
        session_id,
        {
            "sessionUpdate": "agent_thought_chunk",
            "content": text(f"Thinking about turn {n}. {secret}".strip()),
        },
    )
    first = f"t{n}-list"
    notify(
        session_id,
        {
            "sessionUpdate": "tool_call",
            "toolCallId": first,
            "title": "List files",
            "kind": "execute",
            "status": "pending",
            "rawInput": {"command": "ls"},
        },
    )
    answer = call_client(
        "session/request_permission",
        {
            "sessionId": session_id,
            "toolCall": {"toolCallId": first, "title": "List files"},
            "options": [
                {"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"},
                {"optionId": "reject_once", "name": "Reject", "kind": "reject_once"},
            ],
        },
    )
    journal("permission_answer", session=session_id, answer=answer)
    output = "x" * 40000 if tag == "[big]" else f"file_a\nfile_b {secret}".strip()
    for partial in ("file_a", "file_a\nfile_b"):
        notify(
            session_id,
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": first,
                "status": "in_progress",
                "content": [{"type": "content", "content": text(partial)}],
            },
        )
    final = {
        "sessionUpdate": "tool_call_update",
        "toolCallId": first,
        "status": "completed",
        "rawOutput": output,
    }
    notify(session_id, final)
    notify(session_id, final)  # a duplicate terminal update
    notify(
        session_id,
        {
            "sessionUpdate": "tool_call",
            "toolCallId": f"t{n}-read",
            "title": "Read README",
            "kind": "read",
            "status": "completed",
            "rawInput": {"path": "README.md"},
            "rawOutput": "# readme",
        },
    )
    if tag != "[silent]":
        notify(
            session_id,
            {"sessionUpdate": "agent_message_chunk", "content": text("Done: ")},
        )
        notify(
            session_id,
            {
                "sessionUpdate": "agent_message_chunk",
                "content": text(f"listed 2 files. {secret}".strip()),
            },
        )
    return finish_usage(session_id)


def prompt(request_id, params: dict) -> None:
    session_id = params["sessionId"]
    blocks = params.get("prompt") or []
    first_text = next(
        (b.get("text", "") for b in blocks if b.get("type") == "text"), ""
    )
    tag = next(
        (
            t
            for t in (
                "[secret]",
                "[big]",
                "[silent]",
                "[slow]",
                "[hang]",
                "[flaky]",
                "[fail]",
                "[cancel]",
            )
            if t in first_text
        ),
        "",
    )
    n = attempts[session_id] = attempts.get(session_id, 0) + 1
    journal(
        "prompt",
        session=session_id,
        fork=session_id in forks,
        attempt=n,
        tag=tag,
        blocks=[
            {
                "type": b.get("type"),
                "text": (b.get("text") or "")[:20000],
                "mimeType": b.get("mimeType"),
                "uri": b.get("uri"),
                "data_len": len(b.get("data") or ""),
            }
            for b in blocks
        ],
    )
    try:
        if session_id in forks:
            notify(
                session_id,
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": text(f"Fork answer: {first_text[:40]}"),
                },
            )
            respond(request_id, finish_usage(session_id, cost=0.05))
            return
        if tag == "[slow]":
            for _ in range(8):
                time.sleep(0.5)
                notify(
                    session_id,
                    {"sessionUpdate": "agent_thought_chunk", "content": text(".")},
                )
        elif tag == "[hang]":
            deadline = time.time() + 30
            while session_id not in cancelled and time.time() < deadline:
                time.sleep(0.1)
            cancelled.discard(session_id)
            respond(request_id, {"stopReason": "cancelled"})
            return
        elif tag == "[flaky]" and (session_id, "flaky") not in seen_tags:
            seen_tags.add((session_id, "flaky"))
            respond(
                request_id, error={"code": -32603, "message": "Internal error (flaky)"}
            )
            return
        elif tag == "[fail]":
            respond(
                request_id,
                error={"code": -32602, "message": "Prompt rejected: model exploded"},
            )
            return
        elif tag == "[cancel]":
            notify(
                session_id,
                {
                    "sessionUpdate": "tool_call",
                    "toolCallId": f"t{n}-long",
                    "title": "Long build",
                    "kind": "execute",
                    "status": "in_progress",
                    "rawInput": {"command": "make"},
                },
            )
            journal("cancel_waiting", session=session_id)
            deadline = time.time() + 30
            while session_id not in cancelled and time.time() < deadline:
                time.sleep(0.1)
            cancelled.discard(session_id)
            respond(request_id, {"stopReason": "cancelled"})
            return
        respond(request_id, normal_turn(session_id, n, tag))
    except Exception as exc:  # report, do not die
        journal("prompt_crash", error=repr(exc))
        respond(request_id, error={"code": -32603, "message": repr(exc)})


def handle(message: dict) -> None:
    method = message["method"]
    params = message.get("params") or {}
    request_id = message.get("id")
    if method == "initialize":
        stall = float(os.environ.get("FAKE_ACP_INIT_SLEEP") or 0)
        journal("initialize", params=params, stall=stall)
        time.sleep(stall)
        methods = [
            m for m in (os.environ.get("FAKE_ACP_AUTH_METHODS") or "").split(",") if m
        ]
        respond(
            request_id,
            {
                "protocolVersion": 1,
                "agentInfo": {"name": NAME, "version": "9.9.9"},
                "agentCapabilities": {
                    "loadSession": True,
                    "mcpCapabilities": {"http": True, "sse": True},
                    "promptCapabilities": {"image": True},
                },
                "authMethods": [{"id": m, "name": m} for m in methods],
            },
        )
    elif method == "authenticate":
        journal("authenticate", params=params)
        respond(request_id, {})
    elif method == "session/new":
        journal(
            "session_new",
            cwd=params.get("cwd"),
            mcp_servers=params.get("mcpServers"),
            meta=params.get("_meta"),
        )
        if os.environ.get("FAKE_ACP_REQUIRE_KEY") and not os.environ.get(
            "FAKE_ACP_KEY"
        ):
            respond(
                request_id, error={"code": -32000, "message": "Authentication required"}
            )
            return
        session_id = f"sess-{uuid.uuid4().hex[:12]}"
        remember(session_id)
        respond(request_id, {"sessionId": session_id, **session_models(session_id)})
    elif method == "session/load":
        session_id = params.get("sessionId")
        ok = session_id in known_sessions()
        journal(
            "session_load",
            session=session_id,
            known=ok,
            cwd=params.get("cwd"),
            mcp_servers=params.get("mcpServers"),
        )
        if ok:
            respond(request_id, session_models(session_id))
        else:
            respond(request_id, error={"code": -32002, "message": "Session not found"})
    elif method == "session/set_config_option":
        session_id, config_id, value = (
            params.get("sessionId"),
            params.get("configId"),
            params.get("value"),
        )
        journal(
            "set_config_option", session=session_id, config_id=config_id, value=value
        )
        if config_id == "model" and value not in MODELS:
            respond(
                request_id, error={"code": -32602, "message": f"Unknown model {value}"}
            )
            return
        if config_id == "model":
            models[session_id] = value
        respond(request_id, {"configOptions": model_options(session_id)})
    elif method == "session/set_model":
        session_id, value = params.get("sessionId"), params.get("modelId")
        journal("set_model", session=session_id, value=value)
        if value not in MODELS:
            respond(
                request_id, error={"code": -32602, "message": f"Unknown model {value}"}
            )
            return
        models[session_id] = value
        respond(request_id, {})
    elif method == "session/set_mode":
        journal("set_mode", session=params.get("sessionId"), mode=params.get("modeId"))
        respond(request_id, {})
    elif method == "session/fork":
        fork_id = f"fork-{uuid.uuid4().hex[:8]}"
        forks.add(fork_id)
        journal("session_fork", parent=params.get("sessionId"), fork=fork_id)
        respond(request_id, {"sessionId": fork_id})
    elif method == "session/cancel":
        journal("cancel", session=params.get("sessionId"))
        cancelled.add(params.get("sessionId"))
    elif method == "session/prompt":
        prompt(request_id, params)
    elif request_id is not None:
        journal("unknown_method", method=method)
        respond(request_id, error={"code": -32601, "message": "Method not found"})


def main() -> None:
    watched = (
        "FAKE_TOKEN",
        "EMPTY_SECRET",
        "CODEX_HOME",
        "CODEX_API_KEY",
        "OPENAI_API_KEY",
        "CODEX_AUTH_JSON",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_APPLICATION_CREDENTIALS_JSON",
        "npm_config_verify_marker",
        "INIT_CWD",
        "CLAUDECODE",
        "CODEX_CONFIG",
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
    )
    codex_home = os.environ.get("CODEX_HOME")
    journal(
        "start",
        argv=sys.argv[1:],
        cwd=os.getcwd(),
        env={k: os.environ[k] for k in watched if k in os.environ},
        codex_home_files=sorted(os.listdir(codex_home))
        if codex_home and os.path.isdir(codex_home)
        else None,
    )
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        if "method" not in message:  # a response to one of our requests
            slot = pending.get(message.get("id"))
            if slot is not None:
                slot[1] = message.get("result", message.get("error"))
                slot[0].set()
            continue
        threading.Thread(target=handle, args=(message,), daemon=True).start()
    journal("exit")


if __name__ == "__main__":
    main()
