"""Live end-to-end check of the resolve + finalize launch pipeline (#5398).

Starts a real agent-server from an SDK checkout plus the canvas mock LLM (which
records every completion request), then launches conversations through every
source and compares what the model was actually sent.

    python .pr/launch_e2e.py --sdk <sdk checkout> --mock-llm <mock-llm-server.py>

Run it against ``main`` and against this branch to see the before/after.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx


PROFILE_BODY: dict[str, Any] = {
    "agent_kind": "openhands",
    "llm_profile_ref": "mock",
    "tools": [{"name": "terminal"}, {"name": "file_editor"}],
    "system_message_suffix": "PROFILE_SUFFIX_E2E",
    "disabled_skills": ["github"],
    "enable_switch_llm_tool": False,
    "tool_concurrency_limit": 3,
    "mcp_server_refs": [],
    "condenser": {"kind": "NoOpCondenser"},
}
DATETIME_LINE = re.compile(r"(date and time is|current date)[^\n]*", re.IGNORECASE)


class Stack:
    def __init__(self, sdk: Path, mock_llm: Path, port: int, llm_port: int):
        self.home = Path(tempfile.mkdtemp(prefix="launch-e2e-"))
        Path(f"/tmp/ohe{port}").mkdir(exist_ok=True)
        print(f"state: {self.home}", flush=True)
        self.workspace = self.home / "workspace"
        self.workspace.mkdir()
        self.base = f"http://127.0.0.1:{port}"
        self.llm = f"http://127.0.0.1:{llm_port}"
        python = str(sdk / ".venv" / "bin" / "python")
        env = {
            **os.environ,
            "OH_PERSISTENCE_DIR": str(self.home / "persistence"),
            "OH_CONVERSATIONS_PATH": str(self.home / "conversations"),
            "OH_SECRET_KEY": "launch-e2e-secret-key",
            "OPENHANDS_SUPPRESS_BANNER": "1",
            "TMUX_TMPDIR": f"/tmp/ohe{port}",
            "OH_ENABLE_VSCODE": "false",
        }
        self.llm_proc = subprocess.Popen(
            [python, str(mock_llm), "--port", str(llm_port)],
            env=env,
            stdout=(self.home / "mock-llm.log").open("w"),
            stderr=subprocess.STDOUT,
        )
        self.server = subprocess.Popen(
            [python, "-m", "openhands.agent_server", "--port", str(port)],
            env=env,
            cwd=self.home,
            stdout=(self.home / "server.log").open("w"),
            stderr=subprocess.STDOUT,
        )
        self.client = httpx.Client(base_url=self.base, timeout=120)
        self._wait(f"{self.llm}/", self.llm_proc)
        self._wait(f"{self.base}/health", self.server)

    def _wait(self, url: str, process: subprocess.Popen) -> None:
        for _ in range(240):
            if process.poll() is not None:
                sys.exit(f"{url} exited early; see {self.home}")
            try:
                if httpx.get(url, timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        sys.exit(f"{url} never became ready")

    def close(self) -> None:
        for process in (self.server, self.llm_proc):
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()

    def reply_with(self, turns: int = 20) -> None:
        httpx.post(f"{self.llm}/admin/reset")
        httpx.post(
            f"{self.llm}/admin/trajectory/register",
            json={"name": "done", "turns": [{"text": "DONE"}] * turns},
        )
        httpx.post(f"{self.llm}/admin/trajectory/activate", json={"name": "done"})

    def llm_requests(self) -> list[dict[str, Any]]:
        return httpx.get(f"{self.llm}/admin/requests").json()["requests"]


def check(response: httpx.Response, *codes: int) -> Any:
    if response.status_code not in codes:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} -> "
            f"{response.status_code}: {response.text[:400]}"
        )
    return response.json() if response.content else None


def setup(stack: Stack) -> dict[str, str]:
    c = stack.client
    mock_llm = {"model": "openai/mock-test-model", "base_url": stack.llm}
    check(
        c.patch(
            "/api/settings",
            json={
                "agent_settings_diff": {
                    "llm": {**mock_llm, "api_key": "sk-mock"},
                    "agent_context": {"load_memory": True},
                }
            },
        ),
        200,
    )
    for name, model in (
        ("mock", "openai/mock-test-model"),
        ("mock-fast", "openai/mock-fast-model"),
        ("classifier", "openai/mock-classifier-model"),
    ):
        check(
            c.post(
                f"/api/profiles/{name}",
                json={
                    "llm": {**mock_llm, "model": model, "api_key": "sk-mock"},
                    "include_secrets": True,
                },
            ),
            200,
            201,
        )
    for name in ("default", "default-copy"):
        check(c.post(f"/api/agent-profiles/{name}", json=PROFILE_BODY), 201)
    check(
        c.post(
            "/api/agent-profiles/scoped",
            json={**PROFILE_BODY, "secret_refs": ["ALLOWED_TOKEN"]},
        ),
        201,
    )
    check(
        c.post(
            "/api/agent-profiles/broken",
            json={**PROFILE_BODY, "llm_profile_ref": "gone", "mcp_server_refs": ["x"]},
        ),
        201,
    )
    ids = {
        p["name"]: p["id"] for p in check(c.get("/api/agent-profiles"), 200)["profiles"]
    }
    check(c.post(f"/api/agent-profiles/{ids['default']}/activate"), 200)
    return ids


def launch(stack: Stack, **source: Any) -> tuple[int, dict[str, Any] | None]:
    stack.reply_with()
    body = {
        "workspace": {"kind": "LocalWorkspace", "working_dir": str(stack.workspace)},
        "autotitle": False,
        "initial_message": {
            "role": "user",
            "content": [{"type": "text", "text": "hello"}],
            "run": True,
        },
        **source,
    }
    response = stack.client.post(
        "/api/conversations", params={"include_skills": "true"}, json=body
    )
    if response.status_code not in (200, 201):
        return response.status_code, response.json()
    info = response.json()
    for _ in range(120):
        status = stack.client.get(f"/api/conversations/{info['id']}").json()
        if status["execution_status"] not in ("running", "idle"):
            break
        time.sleep(0.5)
    return response.status_code, info


def seen_by_model(stack: Stack) -> dict[str, Any]:
    requests = stack.llm_requests()
    if not requests:
        return {"error": "no LLM request"}
    body = requests[0]
    system = "\n".join(
        part.get("text", "") if isinstance(part, dict) else str(part)
        for message in body["messages"]
        if message["role"] == "system"
        for part in (
            message["content"]
            if isinstance(message["content"], list)
            else [message["content"]]
        )
    )
    stamp = DATETIME_LINE.search(system)
    return {
        "model": body.get("model"),
        "tools": sorted(tool["function"]["name"] for tool in body.get("tools") or []),
        "suffix": "PROFILE_SUFFIX_E2E" in system,
        "gateway_text": "GATEWAY_SYSTEM_E2E" in system,
        "memory": "MEMORY" in system.upper() and "memory" in system,
        "secrets_seen": sorted(
            name for name in ("ALLOWED_TOKEN", "OTHER_TOKEN") if name in system
        ),
        "datetime": stamp.group(0) if stamp else None,
        "system": DATETIME_LINE.sub("<datetime>", system),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--mock-llm", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18490)
    parser.add_argument("--llm-port", type=int, default=18499)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    stack = Stack(args.sdk, args.mock_llm, args.port, args.llm_port)
    rows: dict[str, Any] = {}
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    try:
        ids = setup(stack)

        def record(name: str, **source: Any) -> None:
            try:
                status, info = launch(stack, **source)
            except httpx.HTTPError as exc:
                rows[name] = {"status": "transport error", "detail": repr(exc)}
                return
            row: dict[str, Any] = {"status": status}
            if status in (200, 201) and info is not None:
                row.update(seen_by_model(stack))
                row["launched_agent_profile"] = info.get("launched_agent_profile")
                row["agent_tools"] = [tool["name"] for tool in info["agent"]["tools"]]
                row["load_memory"] = (info["agent"].get("agent_context") or {}).get(
                    "load_memory"
                )
            else:
                row["detail"] = info
            rows[name] = row

        record("default (agent_profile_id)", agent_profile_id=ids["default"])
        record("default-copy (agent_profile_id)", agent_profile_id=ids["default-copy"])
        record(
            "inline agent_profile",
            agent_profile={**PROFILE_BODY, "name": "inline-draft"},
        )
        record(
            "agent_settings (canvas-style, stale timestamp)",
            agent_settings={
                "agent_kind": "openhands",
                "llm": {
                    "model": "openai/mock-test-model",
                    "base_url": stack.llm,
                    "api_key": "sk-mock",
                    "stream": True,
                },
                "tools": PROFILE_BODY["tools"],
                "enable_switch_llm_tool": False,
                "tool_concurrency_limit": 3,
                "condenser": {"kind": "NoOpCondenser"},
                "agent_context": {
                    "system_message_suffix": "PROFILE_SUFFIX_E2E",
                    "disabled_skills": ["github"],
                    "load_project_skills": True,
                    "current_datetime": "2020-01-01T00:00:00+00:00",
                },
            },
        )
        preview = check(
            stack.client.post("/api/agent-profiles/default/materialize"), 200
        )
        resolved = preview["resolved_settings"]
        resolved["llm"]["api_key"] = "sk-mock"
        record("agent_settings (resolved settings of default)", agent_settings=resolved)
        record(
            "raw agent (stale timestamp)",
            agent={
                "kind": "Agent",
                "llm": {
                    "model": "openai/mock-test-model",
                    "base_url": stack.llm,
                    "api_key": "sk-mock",
                },
                "tools": [{"name": "terminal"}],
                "agent_context": {"current_datetime": "2020-01-01T00:00:00+00:00"},
            },
        )
        record(
            "default + llm_profile_ref override",
            agent_profile_id=ids["default"],
            agent_launch_additions={"llm_profile_ref": "mock-fast"},
        )
        record("broken profile (dangling refs)", agent_profile_id=ids["broken"])
        both_secrets = {
            "ALLOWED_TOKEN": {"kind": "StaticSecret", "value": "a"},
            "OTHER_TOKEN": {"kind": "StaticSecret", "value": "b"},
        }
        record(
            "scoped profile (secret_refs=[ALLOWED_TOKEN])",
            agent_profile_id=ids["scoped"],
            secrets=both_secrets,
        )
        record(
            "unscoped profile, same secrets",
            agent_profile_id=ids["default"],
            secrets=both_secrets,
        )

        stack.reply_with()
        gateway = stack.client.post(
            "/v1/chat/completions",
            json={
                "model": "openhands_mock-fast",
                "messages": [
                    {"role": "system", "content": "GATEWAY_SYSTEM_E2E"},
                    {"role": "user", "content": "hello"},
                ],
            },
        )
        rows["OpenAI gateway (model openhands_mock-fast)"] = {
            "status": gateway.status_code,
            **(seen_by_model(stack) if gateway.status_code == 200 else {}),
        }

        rows["materialize default"] = {
            key: preview.get(key)
            for key in (
                "valid",
                "resolved_tools",
                "pending",
                "resolved_skills",
                "errors",
            )
        }
    finally:
        stack.close()

    for name, row in rows.items():
        summary = {k: v for k, v in row.items() if k != "system"}
        print(f"\n## {name}\n{json.dumps(summary, indent=2, default=str)}")

    base = rows["default (agent_profile_id)"]
    comparisons = {
        "default-copy matches default": rows["default-copy (agent_profile_id)"],
        "inline matches default": rows["inline agent_profile"],
        "resolved agent_settings matches default": rows[
            "agent_settings (resolved settings of default)"
        ],
    }
    print("\n# What the model saw, compared with `default`")
    verdicts: dict[str, bool] = {}
    for name, row in comparisons.items():
        same = row.get("status") in (200, 201) and all(
            row.get(key) == base.get(key)
            for key in ("tools", "agent_tools", "suffix", "system", "model")
        )
        verdicts[name] = same
        print(f"- {name}: {'yes' if same else 'NO'}")

    def fresh(row: dict[str, Any]) -> bool:
        stamp = row.get("datetime") or ""
        return today in stamp or today.replace("-", "/") in stamp

    verdicts["agent_settings timestamp fresh"] = fresh(
        rows["agent_settings (canvas-style, stale timestamp)"]
    )
    verdicts["raw agent timestamp fresh"] = fresh(rows["raw agent (stale timestamp)"])
    override = rows["default + llm_profile_ref override"]
    verdicts["override uses mock-fast"] = override.get("model") == (
        "openai/mock-fast-model"
    ) or str(override.get("model", "")).endswith("mock-fast-model")
    broken = rows["broken profile (dangling refs)"]
    detail = (broken.get("detail") or {}).get("detail") or {}
    verdicts["dangling refs -> one 422"] = (
        broken["status"] == 422
        and isinstance(detail, dict)
        and detail.get("dangling_llm_profile_ref") == "gone"
        and detail.get("dangling_mcp_server_refs") == ["x"]
    )
    gw = rows["OpenAI gateway (model openhands_mock-fast)"]
    verdicts["gateway launches the active profile"] = (
        gw["status"] == 200 and bool(gw.get("suffix")) and bool(gw.get("gateway_text"))
    )
    verdicts["gateway uses the model's LLM"] = str(gw.get("model", "")).endswith(
        "mock-fast-model"
    )
    verdicts["materialize tools == launch tools"] = rows["materialize default"].get(
        "resolved_tools"
    ) == base.get("agent_tools")
    verdicts["memory preference on every source"] = all(
        rows[name].get("load_memory") is True
        for name in (
            "default (agent_profile_id)",
            "inline agent_profile",
            "agent_settings (canvas-style, stale timestamp)",
            "raw agent (stale timestamp)",
        )
    )
    verdicts["secret_refs scope the secrets the agent sees"] = rows[
        "scoped profile (secret_refs=[ALLOWED_TOKEN])"
    ].get("secrets_seen") == ["ALLOWED_TOKEN"] and rows[
        "unscoped profile, same secrets"
    ].get("secrets_seen") == ["ALLOWED_TOKEN", "OTHER_TOKEN"]
    print("\n# Verdicts")
    for name, ok in verdicts.items():
        print(f"- {'PASS' if ok else 'FAIL'}  {name}")
    if args.out:
        args.out.write_text(json.dumps({"rows": rows, "verdicts": verdicts}, indent=2))
    return 0 if all(verdicts.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
