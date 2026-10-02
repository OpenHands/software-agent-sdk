"""Live check: a profile's sub-agents stay within the profile's tools and MCP servers.

Starts a real agent-server plus Agent Canvas's scripted mock LLM, launches
conversations by agent_profile_id (and one by agent_settings), has the mock LLM
call the `task` tool, and reads back what the model was offered and told.

    .venv/bin/python .pr/sub_agent_scope_e2e.py \
        --mock <agent-canvas>/tests/e2e/mock-llm/scripts/mock-llm-server.py \
        [--server-python <other checkout>/.venv/bin/python]
"""

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


MOCK_PORT = 19787
SERVER_PORT = 19788
SERVER = f"http://127.0.0.1:{SERVER_PORT}"
MOCK = f"http://127.0.0.1:{MOCK_PORT}"
BUILT_INS = ["bash-runner", "code-explorer", "general-purpose", "web-researcher"]
MCP_HELPER = {
    "name": "mcp-helper",
    "description": "Looks things up through its own fetch MCP server.",
    "tools": ["grep"],
    "mcp_config": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}},
}


def wait_for(url: str, timeout: float = 90) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit(f"timed out waiting for {url}")


def task_call(subagent: str) -> dict:
    return {
        "tool_call": {
            "name": "task",
            "arguments": {"prompt": "Say hi.", "subagent_type": subagent},
        }
    }


def launch(
    client: httpx.Client, workdir: Path, start: dict, turns: list[dict]
) -> tuple[list[str], list[str]]:
    """Return (sub-agents the task tool offered, task results the model saw)."""
    httpx.post(f"{MOCK}/admin/reset", json={}).raise_for_status()
    httpx.post(
        f"{MOCK}/admin/trajectory/register", json={"name": "run", "turns": turns}
    ).raise_for_status()
    httpx.post(
        f"{MOCK}/admin/trajectory/activate", json={"name": "run"}
    ).raise_for_status()
    response = client.post(
        "/api/conversations",
        json={
            **start,
            "autotitle": False,
            "workspace": {"working_dir": str(workdir)},
            "initial_message": {
                "role": "user",
                "content": [{"type": "text", "text": "Delegate."}],
                "run": True,
            },
        },
    )
    response.raise_for_status()
    conversation_id = response.json()["id"]
    deadline = time.time() + 120
    while time.time() < deadline:
        info = client.get(f"/api/conversations/{conversation_id}").json()
        if info["execution_status"] in ("finished", "error", "stuck"):
            break
        time.sleep(0.5)
    else:
        raise SystemExit(f"conversation {conversation_id} did not finish")

    requests = httpx.get(f"{MOCK}/admin/requests").json()["requests"]
    parent_steps = [
        body
        for body in requests
        if any(tool["function"]["name"] == "task" for tool in body.get("tools", []))
    ]
    (task_tool,) = [
        tool["function"]
        for tool in parent_steps[0]["tools"]
        if tool["function"]["name"] == "task"
    ]
    offered = re.findall(r"^- \*\*(.+?)\*\*", task_tool["description"], re.M)
    if not offered:
        offered = re.findall(r"^- (None.*)$", task_tool["description"], re.M)
    results = [
        message["content"]
        if isinstance(message["content"], str)
        else " ".join(block.get("text", "") for block in message["content"])
        for message in parent_steps[-1]["messages"]
        if message["role"] == "tool"
    ]
    return offered, results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", required=True, type=Path)
    parser.add_argument("--server-python", default=sys.executable)
    args = parser.parse_args()

    root = Path(tempfile.mkdtemp(prefix="scope-e2e-"))
    workdir = root / "workspace"
    workdir.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.endswith("SESSION_API_KEY")}
    env.update(
        HOME=str(root / "home"),
        OH_PERSISTENCE_DIR=str(root / "persist"),
        OH_SECRET_KEY=secrets.token_urlsafe(32),
        OPENHANDS_SUPPRESS_BANNER="1",
        # macOS caps unix socket paths at 104 bytes; tmux's default path can exceed it.
        TMUX_TMPDIR=tempfile.mkdtemp(prefix="oht"),
    )
    logs = (root / "mock.log").open("w"), (root / "server.log").open("w")
    procs = [
        subprocess.Popen(
            [sys.executable, str(args.mock), "--port", str(MOCK_PORT)],
            env=env,
            stdout=logs[0],
            stderr=subprocess.STDOUT,
        ),
        subprocess.Popen(
            [args.server_python, "-m", "openhands.agent_server"]
            + ["--host", "127.0.0.1", "--port", str(SERVER_PORT)],
            env=env,
            cwd=root,
            stdout=logs[1],
            stderr=subprocess.STDOUT,
        ),
    ]
    try:
        wait_for(f"{MOCK}/admin/requests")
        wait_for(f"{SERVER}/server_info")
        client = httpx.Client(base_url=SERVER, timeout=60)
        print("agent-server", client.get("/server_info").json().get("version"))

        client.post(
            "/api/profiles/mock",
            json={
                "llm": {
                    "model": "openai/mock-model",
                    "base_url": f"{MOCK}/v1",
                    "api_key": "sk-mock",
                    "usage_id": "agent",
                }
            },
        ).raise_for_status()
        profiles = {
            "read-only": {"tools": ["glob", "grep", "task_tool_set"]},
            "coder": {
                "tools": ["terminal", "file_editor", "task_tracker", "task_tool_set"]
            },
            "grep-all-mcp": {"tools": ["grep", "task_tool_set"]},
            "grep-no-mcp": {
                "tools": ["grep", "task_tool_set"],
                "mcp_server_refs": [],
            },
        }
        for name, body in profiles.items():
            client.post(
                f"/api/agent-profiles/{name}",
                json={
                    "agent_kind": "openhands",
                    "llm_profile_ref": "mock",
                    "mcp_server_refs": body.get("mcp_server_refs"),
                    "tools": [{"name": tool} for tool in body["tools"]],
                },
            ).raise_for_status()
        ids = {
            p["name"]: p["id"]
            for p in client.get("/api/agent-profiles").json()["profiles"]
        }

        diag = client.post("/api/agent-profiles/read-only/materialize").json()
        print(
            "\nmaterialize read-only, resolved task_tool_set:",
            json.dumps(
                [
                    tool
                    for tool in diag["resolved_settings"]["tools"]
                    if tool["name"] == "task_tool_set"
                ]
            ),
        )

        cases = [
            (
                "profile read-only [glob, grep, task_tool_set] -> code-explorer",
                {"agent_profile_id": ids["read-only"]},
                [task_call("code-explorer"), {"text": "sub done"}, {"text": "done"}],
            ),
            (
                "profile coder [terminal, file_editor, task_tracker, task_tool_set]"
                " -> web-researcher, then bash-runner",
                {"agent_profile_id": ids["coder"]},
                [
                    task_call("web-researcher"),
                    task_call("bash-runner"),
                    {"text": "sub done"},
                    {"text": "done"},
                ],
            ),
            (
                "profile grep-all-mcp [grep, task_tool_set], mcp_server_refs=null"
                " -> mcp-helper",
                {"agent_profile_id": ids["grep-all-mcp"]},
                [task_call("mcp-helper"), {"text": "sub done"}, {"text": "done"}],
            ),
            (
                "profile grep-no-mcp [grep, task_tool_set], mcp_server_refs=[]"
                " -> mcp-helper",
                {"agent_profile_id": ids["grep-no-mcp"]},
                [task_call("mcp-helper"), {"text": "sub done"}, {"text": "done"}],
            ),
            (
                "agent_settings (no profile) [glob, grep, task_tool_set]"
                " -> code-explorer",
                {
                    "agent_settings": {
                        "agent_kind": "openhands",
                        "llm": {
                            "model": "openai/mock-model",
                            "base_url": f"{MOCK}/v1",
                            "api_key": "sk-mock",
                        },
                        "tools": [
                            {"name": "glob"},
                            {"name": "grep"},
                            {"name": "task_tool_set"},
                        ],
                    }
                },
                [task_call("code-explorer"), {"text": "sub done"}, {"text": "done"}],
            ),
        ]
        for title, start, turns in cases:
            offered, results = launch(
                client,
                workdir,
                {**start, "agent_definitions": [MCP_HELPER]},
                turns,
            )
            print(f"\n[{title}]")
            print("  offered sub-agents:", offered)
            for result in results:
                print("  task result:", result.strip().replace("\n", " ")[:300])
        return 0
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            proc.wait(timeout=20)


if __name__ == "__main__":
    raise SystemExit(main())
