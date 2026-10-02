"""Live check: delegated sub-agents stay within the delegating agent's tools and MCP.

Runs a real agent-server from this checkout plus Agent Canvas's scripted mock LLM,
launches conversations over the REST API, scripts the model's tool calls, and
asserts on what the model was offered and what each delegation returned. Exits
non-zero if any check fails.

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
import uuid
from pathlib import Path

import httpx


MOCK_PORT = 19797
SERVER_PORT = 19798
SERVER = f"http://127.0.0.1:{SERVER_PORT}"
MOCK = f"http://127.0.0.1:{MOCK_PORT}"
HERE = Path(__file__).resolve().parent
BUILT_INS = {"bash-runner", "code-explorer", "general-purpose", "web-researcher"}
MINI = {"command": sys.executable, "args": [str(HERE / "mini_mcp.py")]}
MINI_OTHER = {"command": sys.executable, "args": [str(HERE / "mini_mcp.py"), "-x"]}
LLM = {"model": "openai/mock-model", "base_url": f"{MOCK}/v1", "api_key": "sk-mock"}


def definition(name: str, tools: list[str], mcp: dict | None = None) -> dict:
    return {
        "name": name,
        "description": f"{name} helper",
        "tools": tools,
        "mcp_config": mcp,
        "system_prompt": f"SUBAGENT-{name}",
    }


DEFINITIONS = [
    definition("grepper", ["grep"]),
    definition("mini-same", ["grep"], {"mini": MINI}),
    definition("mini-diff", ["grep"], {"mini": MINI_OTHER}),
    definition("mini-extra", ["grep"], {"extra": MINI}),
    definition("orchestrator", ["grep", "task_tool_set"]),
]

failures: list[str] = []


def check(label: str, ok: bool, detail: object = "") -> None:
    print(
        f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if not ok else "")
    )
    if not ok:
        failures.append(label)


def task(subagent: str, **extra) -> dict:
    return {
        "tool_call": {
            "name": "task",
            "arguments": {"prompt": "Do it.", "subagent_type": subagent, **extra},
        }
    }


def call(tool: str, **arguments) -> dict:
    return {"tool_call": {"name": tool, "arguments": arguments}}


def say(text: str) -> dict:
    return {"text": text}


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


def system_text(body: dict) -> str:
    content = body["messages"][0]["content"]
    if isinstance(content, str):
        return content
    return " ".join(block.get("text", "") for block in content)


def offered_by(body: dict, tool: str = "task") -> list[str]:
    for spec in body.get("tools", []):
        if spec["function"]["name"] == tool:
            description = spec["function"]["description"]
            return sorted(re.findall(r"^- \*\*(.+?)\*\*", description, re.M))
    raise AssertionError(f"no {tool} tool offered")


def tool_results(body: dict) -> list[str]:
    results = []
    for message in body["messages"]:
        if message["role"] != "tool":
            continue
        content = message["content"]
        if not isinstance(content, str):
            content = " ".join(block.get("text", "") for block in content)
        results.append(content)
    return results


class Run:
    def __init__(self, conversation: dict, requests: list[dict]) -> None:
        self.conversation = conversation
        self.requests = requests
        self.parent = [r for r in requests if "SUBAGENT-" not in system_text(r)]

    def of(self, sub_agent: str) -> list[dict]:
        return [r for r in self.requests if f"SUBAGENT-{sub_agent}" in system_text(r)]

    @property
    def status(self) -> str:
        return self.conversation["execution_status"]


class Server:
    def __init__(self, python: str, mock: Path) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="scope-e2e-"))
        self.workdir = self.root / "workspace"
        self.workdir.mkdir()
        (self.workdir / "notes.txt").write_text("SCOPE_MARKER lives here\n")
        self.env = {
            k: v for k, v in os.environ.items() if not k.endswith("SESSION_API_KEY")
        }
        self.env.update(
            HOME=str(self.root / "home"),
            OH_PERSISTENCE_DIR=str(self.root / "persist"),
            OH_SECRET_KEY=secrets.token_urlsafe(32),
            OPENHANDS_SUPPRESS_BANNER="1",
            # macOS caps unix socket paths at 104 bytes.
            TMUX_TMPDIR=tempfile.mkdtemp(prefix="oht", dir="/tmp"),
        )
        self.python = python
        self.mock = subprocess.Popen(
            [sys.executable, str(mock), "--port", str(MOCK_PORT)],
            env=self.env,
            stdout=(self.root / "mock.log").open("w"),
            stderr=subprocess.STDOUT,
        )
        self.server: subprocess.Popen | None = None
        self.client = httpx.Client(base_url=SERVER, timeout=60)

    def start(self) -> None:
        self.server = subprocess.Popen(
            [self.python, "-m", "openhands.agent_server"]
            + ["--host", "127.0.0.1", "--port", str(SERVER_PORT)],
            env=self.env,
            cwd=self.root,
            stdout=(self.root / "server.log").open("a"),
            stderr=subprocess.STDOUT,
        )
        wait_for(f"{MOCK}/admin/requests")
        wait_for(f"{SERVER}/server_info")

    def stop(self) -> None:
        if self.server is not None:
            self.server.terminate()
            self.server.wait(timeout=30)
            self.server = None

    def close(self) -> None:
        self.stop()
        self.mock.terminate()
        self.mock.wait(timeout=20)

    def script(self, turns: list[dict]) -> None:
        httpx.post(f"{MOCK}/admin/reset", json={}).raise_for_status()
        httpx.post(
            f"{MOCK}/admin/trajectory/register", json={"name": "run", "turns": turns}
        ).raise_for_status()
        httpx.post(
            f"{MOCK}/admin/trajectory/activate", json={"name": "run"}
        ).raise_for_status()

    def wait_idle(self, conversation_id: str) -> Run:
        deadline = time.time() + 180
        while time.time() < deadline:
            info = self.client.get(f"/api/conversations/{conversation_id}").json()
            if info["execution_status"] in ("finished", "error", "stuck", "idle"):
                requests = httpx.get(f"{MOCK}/admin/requests").json()["requests"]
                return Run(info, requests)
            time.sleep(0.5)
        raise SystemExit(f"conversation {conversation_id} did not finish")

    def launch(self, start: dict, turns: list[dict]) -> Run:
        self.script(turns)
        response = self.client.post(
            "/api/conversations",
            json={
                **start,
                "agent_definitions": DEFINITIONS,
                "autotitle": False,
                "workspace": {"working_dir": str(self.workdir)},
                "initial_message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Go."}],
                    "run": True,
                },
            },
        )
        response.raise_for_status()
        return self.wait_idle(response.json()["id"])

    def send(self, conversation_id: str, turns: list[dict]) -> Run:
        self.script(turns)
        self.client.post(
            f"/api/conversations/{conversation_id}/events",
            json={
                "role": "user",
                "content": [{"type": "text", "text": "Again."}],
                "run": True,
            },
        ).raise_for_status()
        time.sleep(1)
        return self.wait_idle(conversation_id)

    def save_profile(self, name: str, tools: list[str] | None, refs=None) -> str:
        body: dict = {"agent_kind": "openhands", "llm_profile_ref": "mock"}
        if tools is not None:
            body["tools"] = [{"name": tool} for tool in tools]
        if refs is not None:
            body["mcp_server_refs"] = refs
        self.client.post(f"/api/agent-profiles/{name}", json=body).raise_for_status()
        return self.profile_id(name)

    def profile_id(self, name: str) -> str:
        for profile in self.client.get("/api/agent-profiles").json()["profiles"]:
            if profile["name"] == name:
                return profile["id"]
        raise AssertionError(f"profile {name} not listed")


def refused(result: str, name: str, missing: str) -> bool:
    return f"Agent '{name}' uses {missing}, which this agent does not have." in result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", required=True, type=Path)
    parser.add_argument("--server-python", default=sys.executable)
    args = parser.parse_args()

    srv = Server(args.server_python, args.mock)
    # A pre-#5151 profile that turned delegation on, migrated at load.
    legacy_dir = srv.root / "persist" / "agent-profiles"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "legacy.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": str(uuid.uuid4()),
                "name": "legacy",
                "llm_profile_ref": "mock",
                "revision": 0,
                "disabled_skills": [],
                "enable_sub_agents": True,
                "enable_switch_llm_tool": True,
            }
        )
    )
    srv.start()
    try:
        c = srv.client
        print("agent-server", c.get("/server_info").json().get("version"))
        c.post("/api/profiles/mock", json={"llm": {**LLM, "usage_id": "agent"}})
        c.post("/api/settings/mcp/mini", json=MINI).raise_for_status()
        ids = {
            "read-only": srv.save_profile(
                "read-only", ["glob", "grep", "task_tool_set"]
            ),
            "coder": srv.save_profile(
                "coder", ["terminal", "file_editor", "task_tracker", "task_tool_set"]
            ),
            "mcp-mini": srv.save_profile(
                "mcp-mini", ["grep", "task_tool_set"], ["mini"]
            ),
            "mcp-none": srv.save_profile("mcp-none", ["grep", "task_tool_set"], []),
            "wf-set": srv.save_profile("wf-set", ["grep", "workflow_tool_set"]),
            "wf-bare": srv.save_profile("wf-bare", ["grep", "workflow"]),
            "legacy": srv.profile_id("legacy"),
        }
        browser = any(
            t["name"] == "browser_tool_set" and t.get("usable")
            for t in c.get("/api/tools/catalog").json()["tools"]
        )
        print("browser usable on this host:", browser)

        print("\n[materialize]")

        def scope(d: dict) -> list:
            return [
                t.get("params", {}).get("sub_agent_scope")
                for t in d["resolved_settings"]["tools"]
                if t["name"] in ("task_tool_set", "workflow_tool_set", "workflow")
            ]

        tools_only = {"tools": True, "mcp_servers": False}
        both = {"tools": True, "mcp_servers": True}
        check(
            "saved read-only: tools scoped, MCP not",
            scope(c.post("/api/agent-profiles/read-only/materialize").json())
            == [tools_only],
        )
        check(
            "saved mcp-mini: tools and MCP scoped",
            scope(c.post("/api/agent-profiles/mcp-mini/materialize").json()) == [both],
        )
        draft = c.post(
            "/api/agent-profiles/read-only/materialize",
            json={
                "profile": {
                    "agent_kind": "openhands",
                    "llm_profile_ref": "mock",
                    "tools": [{"name": "grep"}, {"name": "workflow"}],
                    "mcp_server_refs": [],
                }
            },
        ).json()
        check("draft with bare workflow: scoped", scope(draft) == [both], scope(draft))
        stored = c.get("/api/agent-profiles/read-only").json()["profile"]["tools"]
        check(
            "stored profile has no scope param",
            all(not t.get("params") for t in stored),
            stored,
        )

        print("\n[A] read-only profile [glob, grep, task_tool_set], mcp refs null")
        run = srv.launch(
            {"agent_profile_id": ids["read-only"]},
            [
                task("code-explorer"),
                task("explore"),
                task("grepper"),
                call("grep", pattern="SCOPE_MARKER"),
                say("grepper done"),
                task("bash-runner", resume="task_00000001"),
                say("done"),
            ],
        )
        a_id = run.conversation["id"]
        results = tool_results(run.parent[-1])
        check("finished", run.status == "finished", run.status)
        check(
            "offered: forwarded helpers only, no built-ins",
            offered_by(run.parent[0])
            == ["grepper", "mini-diff", "mini-extra", "mini-same", "orchestrator"],
            offered_by(run.parent[0]),
        )
        check("code-explorer refused", refused(results[0], "code-explorer", "terminal"))
        check(
            "deprecated alias 'explore' refused as code-explorer",
            refused(results[1], "code-explorer", "terminal"),
            results[1][:200],
        )
        check("grepper ran", "grepper done" in results[2], results[2][:200])
        check(
            "grepper's own grep call found the file",
            any("notes.txt" in r for r in tool_results(run.of("grepper")[-1])),
        )
        check(
            "resuming that task as bash-runner refused",
            refused(results[3], "bash-runner", "terminal"),
            results[3][:200],
        )
        info = c.get(f"/api/conversations/{a_id}").json()
        persisted = [t for t in info["agent"]["tools"] if t["name"] == "task_tool_set"]
        check(
            "conversation's agent carries the scope",
            bool(persisted)
            and persisted[0]["params"].get("sub_agent_scope") == tools_only,
            persisted,
        )

        print(
            "\n[B] coder profile [terminal, file_editor, task_tracker, task_tool_set]"
        )
        run = srv.launch(
            {"agent_profile_id": ids["coder"]},
            [
                task("web-researcher"),
                task("bash-runner"),
                call("terminal", command="echo BASH_$((40+2))"),
                say("bash done"),
                say("done"),
            ],
        )
        results = tool_results(run.parent[-1])
        bash_requests = [
            r
            for r in run.requests
            if "command-line execution specialist" in system_text(r)
        ]
        check("finished", run.status == "finished", run.status)
        check(
            "offered: built-ins it can run, not web-researcher or grep helpers",
            offered_by(run.parent[0])
            == ["bash-runner", "code-explorer", "general-purpose"],
            offered_by(run.parent[0]),
        )
        check(
            "web-researcher refused",
            refused(results[0], "web-researcher", "browser_tool_set"),
        )
        check("bash-runner ran", "bash done" in results[1], results[1][:200])
        check(
            "bash-runner's terminal really ran",
            bool(bash_requests)
            and any("BASH_42" in r for r in tool_results(bash_requests[-1])),
        )

        print("\n[C] mcp-mini profile [grep, task_tool_set], mcp_server_refs=['mini']")
        run = srv.launch(
            {"agent_profile_id": ids["mcp-mini"]},
            [
                task("mini-diff"),
                task("mini-extra"),
                task("mini-same"),
                say("same done"),
                say("done"),
            ],
        )
        results = tool_results(run.parent[-1])
        check("finished", run.status == "finished", run.status)
        check(
            "offered: same-config MCP helper, not a different or extra server",
            offered_by(run.parent[0]) == ["grepper", "mini-same", "orchestrator"],
            offered_by(run.parent[0]),
        )
        check(
            "same name, other config refused",
            refused(results[0], "mini-diff", "MCP server 'mini'"),
            results[0][:200],
        )
        check(
            "extra server refused",
            refused(results[1], "mini-extra", "MCP server 'extra'"),
            results[1][:200],
        )
        check("same server ran", "same done" in results[2], results[2][:200])

        print("\n[D] mcp-none profile [grep, task_tool_set], mcp_server_refs=[]")
        run = srv.launch(
            {"agent_profile_id": ids["mcp-none"]},
            [task("mini-same"), say("done")],
        )
        results = tool_results(run.parent[-1])
        check(
            "offered: no MCP helpers",
            offered_by(run.parent[0]) == ["grepper", "orchestrator"],
            offered_by(run.parent[0]),
        )
        check(
            "MCP helper refused",
            refused(results[0], "mini-same", "MCP server 'mini'"),
            results[0][:200],
        )

        print("\n[E] nested: orchestrator delegates under the inherited scope")
        run = srv.launch(
            {"agent_profile_id": ids["mcp-mini"]},
            [
                task("orchestrator"),
                task("bash-runner"),
                task("mini-same"),
                say("orch done"),
                say("done"),
            ],
        )
        orchestrator = run.of("orchestrator")
        nested = tool_results(orchestrator[-1]) if orchestrator else []
        check("finished", run.status == "finished", run.status)
        check(
            "orchestrator is offered only what it holds",
            bool(orchestrator)
            and offered_by(orchestrator[0]) == ["grepper", "orchestrator"],
            orchestrator and offered_by(orchestrator[0]),
        )
        check(
            "nested bash-runner refused",
            len(nested) == 2 and refused(nested[0], "bash-runner", "terminal"),
            nested,
        )
        check(
            "nested MCP helper refused (orchestrator has no MCP server)",
            len(nested) == 2 and refused(nested[1], "mini-same", "MCP server 'mini'"),
            nested,
        )
        check(
            "orchestrator finished",
            "orch done" in tool_results(run.parent[-1])[0],
        )

        for name in ("wf-set", "wf-bare"):
            tool = "workflow"
            print(f"\n[F] {name} profile: workflow delegation")
            refused_script = (
                "async def main(wf):\n"
                "    return await wf.run_agent('x', 'code-explorer')"
            )
            allowed_script = (
                "async def main(wf):\n    return await wf.run_agent('x', 'grepper')"
            )
            run = srv.launch(
                {"agent_profile_id": ids[name]},
                [
                    call(tool, name="r", script=refused_script),
                    call(tool, name="a", script=allowed_script),
                    say("wf done"),
                    say("done"),
                ],
            )
            results = tool_results(run.parent[-1])
            check("finished", run.status == "finished", run.status)
            check(
                "workflow refuses code-explorer",
                "Agent 'code-explorer' uses terminal" in results[0],
                results[0][:200],
            )
            check("workflow runs grepper", "wf done" in results[1], results[1][:200])

        print("\n[G] legacy v2 profile with enable_sub_agents: true")
        legacy = c.get("/api/agent-profiles/legacy").json()["profile"]
        print("  migrated tools:", [t["name"] for t in legacy["tools"] or []])
        expected = {"bash-runner", "code-explorer", "general-purpose"}
        if browser:
            expected.add("web-researcher")
        run = srv.launch(
            {"agent_profile_id": ids["legacy"]},
            [task("general-purpose"), say("gp done"), say("done")],
        )
        results = tool_results(run.parent[-1])
        offered = set(offered_by(run.parent[0]))
        check(
            "offered: every built-in its pinned tools allow, no grep helpers",
            offered == expected,
            sorted(offered),
        )
        check(
            "web-researcher offered iff the browser is usable",
            ("web-researcher" in offered) == browser,
            sorted(offered),
        )
        check("general-purpose ran", "gp done" in results[0], results[0][:200])

        print("\n[H] agent_settings launch, no profile: unchanged")
        run = srv.launch(
            {
                "agent_settings": {
                    "agent_kind": "openhands",
                    "llm": LLM,
                    "tools": [
                        {"name": "glob"},
                        {"name": "grep"},
                        {"name": "task_tool_set"},
                    ],
                }
            },
            [
                task("code-explorer"),
                call("terminal", command="echo EXPLORED"),
                say("explored"),
                say("done"),
            ],
        )
        results = tool_results(run.parent[-1])
        check(
            "every sub-agent offered",
            BUILT_INS <= set(offered_by(run.parent[0])),
            offered_by(run.parent[0]),
        )
        check("code-explorer ran", "explored" in results[0], results[0][:200])

        print("\n[I] explicit agent launch")
        agent = {
            "kind": "Agent",
            "llm": LLM,
            "tools": [
                {"name": "grep"},
                {"name": "task_tool_set", "params": {"sub_agent_scope": tools_only}},
            ],
        }
        run = srv.launch({"agent": agent}, [task("bash-runner"), say("done")])
        check(
            "client-set scope is honoured",
            refused(tool_results(run.parent[-1])[0], "bash-runner", "terminal"),
        )
        bad = {
            **agent,
            "tools": [
                {"name": "task_tool_set", "params": {"sub_agent_scope": {"x": True}}}
            ],
        }
        srv.script([say("done")])
        response = c.post(
            "/api/conversations",
            json={
                "agent": bad,
                "autotitle": False,
                "workspace": {"working_dir": str(srv.workdir)},
                "initial_message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Go."}],
                    "run": True,
                },
            },
        )
        print(f"  invalid scope param -> HTTP {response.status_code}")
        if response.status_code >= 400:
            check(
                "invalid scope rejected at start, like any bad tool param",
                "SubAgentScope" in response.text,
                response.text[:200],
            )
        else:
            status = srv.wait_idle(response.json()["id"]).status
            check("invalid scope does not run", status != "finished", status)
        check(
            "server still healthy",
            httpx.get(f"{SERVER}/server_info").status_code == 200,
        )

        print("\n[J] restart the server and resume conversation A")
        srv.stop()
        srv.start()
        run = srv.send(
            a_id, [task("bash-runner"), task("grepper"), say("ok"), say("done")]
        )
        results = tool_results(run.parent[-1])[-2:]
        check("finished", run.status == "finished", run.status)
        check(
            "after restart: offered list unchanged",
            offered_by(run.parent[0])
            == ["grepper", "mini-diff", "mini-extra", "mini-same", "orchestrator"],
            offered_by(run.parent[0]),
        )
        check(
            "after restart: bash-runner still refused",
            refused(results[0], "bash-runner", "terminal"),
            results[0][:200],
        )
        check("after restart: grepper still runs", "ok" in results[1], results[1][:200])

        print(f"\n{len(failures)} failure(s)" + (f": {failures}" if failures else ""))
        return 1 if failures else 0
    finally:
        srv.close()
        print("logs:", srv.root)


if __name__ == "__main__":
    raise SystemExit(main())
