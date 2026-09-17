"""Live end-to-end check of the unified launch pipeline (#5141).

Starts a real agent-server, stores two identically-configured Agent Profiles —
one named ``default`` — and launches a conversation through every product path:
``agent_profile_id``, an inline ``agent_profile`` draft, and the deprecated
``agent_settings``. Then compares the agents the server actually built, plus
the ``materialize`` preview of the same profile.

Run:  uv run python .pr/launch_parity_e2e.py
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx


PORT = 8971
BASE = f"http://127.0.0.1:{PORT}"

MCP_CONFIG = {
    "fetch": {"url": "https://fetch.invalid/mcp"},
    "other": {"url": "https://other.invalid/mcp"},
}
PROFILE_BODY: dict[str, Any] = {
    "agent_kind": "openhands",
    "llm_profile_ref": "primary",
    "tools": [{"name": "terminal"}, {"name": "glob"}],
    "system_message_suffix": "PROFILE_SUFFIX",
    "disabled_skills": ["git"],
    "enable_switch_llm_tool": False,
    "tool_concurrency_limit": 3,
    "mcp_server_refs": ["fetch"],
    "condenser": {"kind": "NoOpCondenser", "enabled": False},
}


def start_server(home: Path) -> subprocess.Popen:
    env = {
        **os.environ,
        "OH_PERSISTENCE_DIR": str(home / "persistence"),
        "OPENHANDS_SUPPRESS_BANNER": "1",
    }
    log = (home / "server.log").open("w")
    process = subprocess.Popen(
        [sys.executable, "-m", "openhands.agent_server", "--port", str(PORT)],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    for _ in range(120):
        if process.poll() is not None:
            sys.exit(f"server exited early; see {home / 'server.log'}")
        try:
            if httpx.get(f"{BASE}/health", timeout=2).status_code == 200:
                return process
        except httpx.HTTPError:
            time.sleep(0.5)
    sys.exit("server did not become healthy")


def expect(response: httpx.Response, *codes: int) -> Any:
    if response.status_code not in codes:
        sys.exit(
            f"{response.request.method} {response.request.url} "
            f"-> {response.status_code}: {response.text[:500]}"
        )
    return response.json() if response.content else None


def agent_view(agent: dict[str, Any]) -> dict[str, Any]:
    """The agent fields a profile owns, minus per-launch identity."""
    context = agent.get("agent_context") or {}
    llm = dict(agent["llm"])
    # api_key is masked on a launch and redacted in a preview; usage ids and
    # metrics are per-conversation.
    for volatile in ("usage_id", "metrics", "service_id", "api_key"):
        llm.pop(volatile, None)
    return {
        "llm": llm,
        "tools": [tool["name"] for tool in agent["tools"]],
        "mcp_config": sorted(agent.get("mcp_config") or {}),
        "skills": sorted(skill["name"] for skill in context.get("skills") or []),
        "system_message_suffix": context.get("system_message_suffix"),
        "disabled_skills": context.get("disabled_skills"),
        "load_project_skills": context.get("load_project_skills"),
        "load_memory": context.get("load_memory"),
        "condenser_off": agent.get("condenser") is None,
        "critic": agent.get("critic"),
        "tool_concurrency_limit": agent.get("tool_concurrency_limit"),
        "switch_llm": "SwitchLLMTool" in (agent.get("include_default_tools") or []),
        "has_datetime": bool(context.get("current_datetime")),
    }


def launch(client: httpx.Client, workspace: Path, **source: Any) -> dict[str, Any]:
    body = {
        "workspace": {"kind": "LocalWorkspace", "working_dir": str(workspace)},
        **source,
    }
    return expect(
        client.post("/api/conversations", params={"include_skills": "true"}, json=body),
        200,
        201,
    )


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="launch-parity-"))
    workspace = home / "workspace"
    workspace.mkdir()
    process = start_server(home)
    try:
        client = httpx.Client(base_url=BASE, timeout=120)

        # Global settings: the shared MCP list plus a legacy inline LLM.
        expect(
            client.patch(
                "/api/settings",
                json={
                    "agent_settings_diff": {
                        "llm": {"model": "gpt-4o", "api_key": "sk-e2e"},
                        "mcp_config": MCP_CONFIG,
                    }
                },
            ),
            200,
        )
        expect(
            client.post(
                "/api/profiles/primary",
                json={
                    "llm": {"model": "gpt-4o", "api_key": "sk-e2e"},
                    "include_secrets": True,
                },
            ),
            200,
            201,
        )

        # Two profiles, identical but for the name.
        for name in ("default", "default-copy"):
            expect(client.post(f"/api/agent-profiles/{name}", json=PROFILE_BODY), 201)
        listed = expect(client.get("/api/agent-profiles"), 200)
        ids = {p["name"]: p["id"] for p in listed["profiles"]}

        results: dict[str, dict[str, Any]] = {}
        results["default (agent_profile_id)"] = agent_view(
            launch(client, workspace, agent_profile_id=ids["default"])["agent"]
        )
        results["default-copy (agent_profile_id)"] = agent_view(
            launch(client, workspace, agent_profile_id=ids["default-copy"])["agent"]
        )
        results["inline (agent_profile)"] = agent_view(
            launch(
                client,
                workspace,
                agent_profile={**PROFILE_BODY, "name": "inline-draft"},
            )["agent"]
        )

        # The deprecated path: the same configuration as an agent_settings dump.
        legacy = {
            "agent_kind": "openhands",
            "llm": {"model": "gpt-4o", "api_key": "sk-e2e"},
            "tools": PROFILE_BODY["tools"],
            "enable_switch_llm_tool": False,
            "tool_concurrency_limit": 3,
            "mcp_config": {"fetch": MCP_CONFIG["fetch"]},
            "condenser": PROFILE_BODY["condenser"],
            "agent_context": {
                "system_message_suffix": "PROFILE_SUFFIX",
                "disabled_skills": ["git"],
                "current_datetime": "2020-01-01T00:00",
            },
        }
        legacy_agent = launch(client, workspace, agent_settings=legacy)["agent"]
        results["legacy (agent_settings)"] = agent_view(legacy_agent)
        legacy_datetime = (legacy_agent["agent_context"] or {})["current_datetime"]
        print(
            f"legacy current_datetime sent 2020-01-01T00:00, launched with "
            f"{legacy_datetime}"
        )
        stale_timestamp = str(legacy_datetime).startswith("2020")

        # The preview of the same profile.
        preview = expect(client.post("/api/agent-profiles/default/materialize"), 200)
        if not preview["valid"]:
            sys.exit(f"materialize invalid: {preview['errors']}")
        previewed = preview["resolved_settings"]
        results["materialize (default)"] = agent_view(
            {
                "llm": previewed["llm"],
                "tools": previewed["tools"],
                "mcp_config": previewed["mcp_config"],
                "agent_context": previewed["agent_context"],
                # A disabled condenser builds no condenser on the agent.
                "condenser": None if not previewed["condenser"]["enabled"] else {},
                "critic": None,
                "tool_concurrency_limit": previewed["tool_concurrency_limit"],
                "include_default_tools": (
                    ["SwitchLLMTool"] if previewed["enable_switch_llm_tool"] else []
                ),
            }
        )

        # A dangling reference must fail the launch with a structured error.
        expect(
            client.post(
                "/api/agent-profiles/broken",
                json={
                    **PROFILE_BODY,
                    "llm_profile_ref": "gone",
                    "mcp_server_refs": ["nope"],
                },
            ),
            201,
        )
        broken_id = {
            p["name"]: p["id"]
            for p in expect(client.get("/api/agent-profiles"), 200)["profiles"]
        }["broken"]
        error = client.post(
            "/api/conversations",
            json={
                "agent_profile_id": broken_id,
                "workspace": {"kind": "LocalWorkspace", "working_dir": str(workspace)},
            },
        )

        # The deprecated payload carries the client's own skill catalog (canvas
        # assembles one today), so that field is the client's, not the pipeline's.
        baseline_key = "default (agent_profile_id)"
        baseline = results[baseline_key]
        print(f"\nbaseline: {baseline_key}")
        print(json.dumps(baseline, indent=2, sort_keys=True))
        failures = []
        for name, view in results.items():
            if name == baseline_key:
                continue
            ignored = {"skills"} if name == "legacy (agent_settings)" else set()
            diff = {
                field: (baseline[field], view[field])
                for field in baseline
                if field not in ignored and baseline[field] != view[field]
            }
            print(f"\n{name}: {'MATCH' if not diff else 'DIFF ' + json.dumps(diff)}")
            if diff:
                failures.append(name)

        print(f"\ndangling refs -> HTTP {error.status_code} {error.text[:300]}")
        if error.status_code != 422:
            failures.append("dangling refs status")
        detail = error.json().get("detail", {})
        if detail.get("dangling_llm_profile_ref") != "gone" or detail.get(
            "dangling_mcp_server_refs"
        ) != ["nope"]:
            failures.append("dangling refs detail")

        if stale_timestamp:
            failures.append("stale current_datetime")

        print("\nRESULT:", "FAIL " + ", ".join(failures) if failures else "PASS")
        return 1 if failures else 0
    finally:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.copy(home / "server.log", "/tmp/launch-parity-server.log")
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
