"""Drive a RemoteWorkspace against a real agent server started on localhost
(the repo's `openhands.agent_server`), the way a program uses a sandbox once
`DockerWorkspace` has started one.

Usage: remote_workspace.py ops|settings|conversation|repos
  ops           client, auth, commands (long, concurrent, timeout), files,
                git, alive, AsyncRemoteWorkspace
  settings      get_llm / get_secrets / get_mcp_config from server settings
  conversation  Conversation(workspace=RemoteWorkspace) with a scripted model
                endpoint, runtime-scoped workspace, completion callback
  repos         clone_repos (file:// repos), get_repos_context, skills
"""

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path

import httpx
from sdk_verify import emit, run_paths
from servers import AgentServer, CaptureServer, FakeLLMServer, free_port

from openhands.sdk.workspace import RemoteWorkspace, Workspace


mode = sys.argv[1]
run = run_paths()["run"]
KEY = "session-key-for-verify"
server_env = {
    "SESSION_API_KEY": KEY,
    "OH_SECRET_KEY": "verify-secret-key-0123456789abcdef",
}
remote_dir = run / "remote-workspace"
remote_dir.mkdir(exist_ok=True)
result: dict = {"ok": True, "mode": mode}


def text(r) -> str:
    return (r.stdout or "").strip()


def git_env() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "v",
        "GIT_AUTHOR_EMAIL": "v@example.com",
        "GIT_COMMITTER_NAME": "v",
        "GIT_COMMITTER_EMAIL": "v@example.com",
    }


def ops(server: AgentServer) -> None:
    try:
        Workspace(host=server.base_url, working_dir=remote_dir, api_key=KEY)
        result["path_working_dir_error"] = None
    except Exception as exc:
        result["path_working_dir_error"] = f"{type(exc).__name__}: {exc}".splitlines()[
            0
        ]
    ws = Workspace(host=server.base_url + "/", working_dir=str(remote_dir), api_key=KEY)
    result["type"] = type(ws).__name__
    result["host_normalized"] = ws.host == server.base_url
    result["client_lazy"] = ws._client is None
    result["client_base_url"] = str(ws.client.base_url).rstrip("/") == server.base_url
    result["sends_session_key"] = ws.client.headers.get("X-Session-API-Key") == KEY
    ws.reset_client()
    result["reset_client_cleared"] = ws._client is None

    bad = RemoteWorkspace(
        host=server.base_url, working_dir=str(remote_dir), api_key="wrong"
    )
    # Command errors come back as a CommandResult, not an exception.
    denied = bad.execute_command("true")
    result["wrong_key"] = {"exit": denied.exit_code, "stderr": denied.stderr[:200]}
    result["wrong_key_alive"] = bad.alive

    r = ws.execute_command("echo hi && pwd", cwd=remote_dir)
    result["exec"] = {"exit": r.exit_code, "lines": text(r).split()}
    result["exec_pwd_ok"] = text(r).split()[-1] == str(remote_dir)
    failing = ws.execute_command("echo bad >&2; exit 4")
    result["failing_exit"] = failing.exit_code
    result["failing_output"] = (failing.stdout + failing.stderr).strip()

    # Output spread over many polls must arrive once, in order.
    long = ws.execute_command(
        "for i in $(seq 1 40); do echo line$i; sleep 0.05; done", timeout=60
    )
    lines = text(long).splitlines()
    result["long_lines"] = len(lines)
    result["long_unique_in_order"] = lines == [f"line{i}" for i in range(1, 41)]

    # Two commands at once keep their own output.
    outs: dict[str, str] = {}

    def worker(tag: str) -> None:
        cmd = f"for i in 1 2 3 4 5; do echo {tag}$i; sleep 0.1; done"
        outs[tag] = text(ws.execute_command(cmd, timeout=60))

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    result["concurrent_isolated"] = outs.get("A") == "\n".join(
        f"A{i}" for i in range(1, 6)
    ) and outs.get("B") == "\n".join(f"B{i}" for i in range(1, 6))

    slow = ws.execute_command("sleep 20", timeout=2)
    result["timeout_occurred"] = slow.timeout_occurred

    # Files through the server, with Path and bytes sources.
    local = run / "local-files"
    local.mkdir(exist_ok=True)
    (local / "up.txt").write_text("uploaded\n")
    up = ws.file_upload(local / "up.txt", remote_dir / "sub" / "up.txt")
    up_bytes = ws.file_upload(b"raw bytes\n", str(remote_dir / "bytes.txt"))
    down = ws.file_download(remote_dir / "sub" / "up.txt", local / "down.txt")
    result["upload_ok"] = up.success and (remote_dir / "sub" / "up.txt").exists()
    result["upload_bytes_ok"] = up_bytes.success and (
        (remote_dir / "bytes.txt").read_text() == "raw bytes\n"
    )
    result["download_ok"] = (
        down.success and (local / "down.txt").read_text() == "uploaded\n"
    )
    missing = ws.file_download(remote_dir / "nope.txt", local / "nope.txt")
    result["missing_download_success"] = missing.success

    # Git state of a repository the workspace's commands created.
    repo = remote_dir / "repo"
    ws.execute_command(
        f"mkdir -p {repo} && cd {repo} && git init -q -b main && echo one > f.txt"
        " && git add -A && git -c user.name=v -c user.email=v@e commit -qm init"
        " && echo two >> f.txt && echo n > new.txt",
        timeout=30,
    )
    changes = ws.git_changes(repo)
    diff = ws.git_diff(repo / "f.txt")
    result["git_changes"] = sorted(
        f"{c.status.value}:{Path(c.path).name}" for c in changes
    )
    result["git_diff_modified"] = diff.modified

    result["alive"] = ws.alive
    result["alive_wrong_port"] = RemoteWorkspace(
        host=f"http://127.0.0.1:{free_port()}", working_dir="/tmp"
    ).alive

    # The async twin.
    from openhands.sdk.workspace.remote.async_remote_workspace import (
        AsyncRemoteWorkspace,
    )

    async def use_async() -> dict:
        async with AsyncRemoteWorkspace(
            host=server.base_url, working_dir=str(remote_dir), api_key=KEY
        ) as aws:
            r = await aws.execute_command("echo async-hi", cwd=str(remote_dir))
            (local / "a.txt").write_text("async\n")
            up = await aws.file_upload(local / "a.txt", remote_dir / "async.txt")
            down = await aws.file_download(remote_dir / "async.txt", local / "a2.txt")
            ch = await aws.git_changes(repo)
            d = await aws.git_diff(repo / "f.txt")
            return {
                "exec": text(r),
                "exit": r.exit_code,
                "upload": up.success,
                "download": down.success
                and (local / "a2.txt").read_text() == "async\n",
                "git_changes": len(ch),
                "git_diff_modified": d.modified,
                "alive": aws.alive,
            }

    result["async"] = asyncio.run(use_async())
    ws.reset_client()
    server.stop()
    result["alive_after_stop"] = ws.alive


def settings(server: AgentServer) -> None:
    admin = httpx.Client(base_url=server.base_url, headers={"X-Session-API-Key": KEY})
    llm_cfg = {
        "model": "openai/settings-model",
        "base_url": "http://127.0.0.1:9/v1",
        "api_key": "llm-secret-123",
    }
    r = admin.patch("/api/settings", json={"agent_settings_diff": {"llm": llm_cfg}})
    result["patch_status"] = r.status_code
    r = admin.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "mcp_config": {
                    "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}
                },
            }
        },
    )
    result["patch_mcp_status"] = r.status_code
    if r.status_code != 200:
        result["patch_error"] = r.text[:400]
    admin.put("/api/settings/secrets", json={"name": "PROJECT_TOKEN", "value": "tok-1"})
    admin.put("/api/settings/secrets", json={"name": "OTHER", "value": "tok-2"})

    ws = RemoteWorkspace(host=server.base_url, working_dir=str(remote_dir), api_key=KEY)
    llm = ws.get_llm()
    result["get_llm"] = {
        "model": llm.model,
        "base_url": llm.base_url,
        "key_ok": llm.api_key is not None
        and llm.api_key.get_secret_value() == "llm-secret-123",
    }
    over = ws.get_llm(model="openai/override", temperature=0.3)
    result["get_llm_override"] = {"model": over.model, "temperature": over.temperature}

    r = admin.post(
        "/api/profiles/fast",
        json={
            "llm": {**llm_cfg, "model": "openai/fast-model"},
            "include_secrets": True,
        },
    )
    result["profile_save_status"] = r.status_code
    named = ws.get_llm(profile_name="fast")
    result["get_llm_profile"] = {"model": named.model, "usage_id": named.usage_id}
    admin.post("/api/profiles/fast/activate")
    active = ws.get_llm()
    result["get_llm_active_profile"] = active.model
    try:
        ws.get_llm(profile_name="missing-profile")
        result["missing_profile_error"] = None
    except Exception as exc:
        result["missing_profile_error"] = type(exc).__name__

    secrets = ws.get_secrets()
    token = secrets.get("PROJECT_TOKEN")
    result["secrets"] = sorted(secrets)
    result["secret_type"] = type(token).__name__ if token else None
    result["secret_url_ok"] = bool(token) and token.url.endswith(
        "/api/settings/secrets/PROJECT_TOKEN"
    )
    result["secret_has_session_header"] = (
        bool(token) and (token.headers or {}).get("X-Session-API-Key") == KEY
    )
    result["secret_resolves"] = bool(token) and token.get_value() == "tok-1"
    result["secrets_filtered"] = sorted(ws.get_secrets(names=["OTHER"]))
    mcp = ws.get_mcp_config()
    result["mcp_servers"] = sorted(mcp)
    result["mcp_fetch_command"] = getattr(mcp.get("fetch"), "command", None)
    result["secret_value_missing"] = ws._get_secret_value("NOT_THERE")
    result["secret_value_present"] = ws._get_secret_value("PROJECT_TOKEN")


def conversation(server: AgentServer) -> None:
    from openhands.sdk import Agent, Conversation, RemoteConversation, Tool
    from openhands.sdk.event import ActionEvent, ObservationEvent
    from openhands.tools.terminal import TerminalTool

    script = [
        {
            "text": "Writing.",
            "tool": "terminal",
            "args": {"command": "echo remote-agent > made_by_agent.txt && pwd"},
        },
        {"finish": "made_by_agent.txt written"},
    ]
    with FakeLLMServer(script) as fake, CaptureServer() as callback:
        os.environ["AUTOMATION_CALLBACK_URL"] = f"{callback.base_url}/cb"
        os.environ["AUTOMATION_RUN_ID"] = "remote-run-1"
        ws = RemoteWorkspace(
            host=server.base_url, working_dir=str(remote_dir), api_key=KEY
        )
        with ws:
            agent = Agent(llm=fake.llm(), tools=[Tool(name=TerminalTool.name)])
            events = []
            conv = Conversation(
                agent=agent, workspace=ws, callbacks=[events.append], visualizer=None
            )
            result["conversation_type"] = type(conv).__name__
            result["is_remote"] = isinstance(conv, RemoteConversation)
            cid = str(conv.state.id)
            conv.send_message("write the file")
            conv.run()
            result["status"] = str(conv.state.execution_status.value)
            remote_events = list(conv.state.events)
            result["actions"] = sum(isinstance(e, ActionEvent) for e in remote_events)
            result["observations"] = sum(
                isinstance(e, ObservationEvent) for e in remote_events
            )
            result["callback_events"] = len(events)
            result["llm_requests"] = len(fake.requests)
            result["registered_conversation"] = ws.conversation_id == cid

            # A workspace scoped to that conversation's runtime.
            scoped = RemoteWorkspace(
                host=server.base_url,
                working_dir=str(remote_dir),
                api_key=KEY,
                runtime_conversation_id=cid,
            )
            result["scoped_prefix"] = scoped.api_prefix == f"/api/conversations/{cid}"
            sr = scoped.execute_command(
                "cat made_by_agent.txt && pwd", cwd=str(remote_dir)
            )
            result["scoped_exec"] = text(sr).split()
            (run / "s.txt").write_text("scoped\n")
            result["scoped_upload"] = scoped.file_upload(
                run / "s.txt", remote_dir / "scoped.txt"
            ).success
            result["scoped_outside_upload"] = scoped.file_upload(
                run / "s.txt", "/tmp/outside-scope.txt"
            ).success
            try:
                scoped.get_runtime_session_key()
                result["runtime_key_error"] = None
            except Exception as exc:
                result["runtime_key_error"] = type(exc).__name__
            unscoped_release = None
            try:
                ws.release_runtime()
            except ValueError as exc:
                unscoped_release = str(exc)
            result["unscoped_release_error"] = unscoped_release
            other = RemoteWorkspace(
                host=server.base_url,
                working_dir=str(remote_dir),
                api_key=KEY,
                runtime_conversation_id="00000000-0000-0000-0000-000000000000",
            )
            unknown = other.execute_command("true")
            result["unknown_scope"] = {
                "exit": unknown.exit_code,
                "stderr": unknown.stderr[:200],
            }
            conv.close()
        result["agent_file"] = (
            (remote_dir / "made_by_agent.txt").read_text().strip()
            if (remote_dir / "made_by_agent.txt").exists()
            else None
        )
        posts = [r for r in callback.requests if r["path"] == "/cb"]
        body = posts[0]["body"] if posts else {}
        result["callback"] = {
            "posts": len(posts),
            "status": body.get("status"),
            "conversation_id_ok": body.get("conversation_id") == cid,
            "run_id": body.get("run_id"),
            "has_cost": "cost" in body,
        }
        for name in ("AUTOMATION_CALLBACK_URL", "AUTOMATION_RUN_ID"):
            os.environ.pop(name)


def repos(server: AgentServer) -> None:
    from openhands.sdk.workspace import RemoteWorkspace as RW
    from openhands.sdk.workspace.repo import RepoSource

    origin = run / "origins"
    for name, files in (
        ("alpha", {"README.md": "alpha\n"}),
        ("beta", {"README.md": "beta\n"}),
        ("gamma", {"README.md": "gamma\n"}),
    ):
        d = origin / name
        d.mkdir(parents=True, exist_ok=True)
        for f, c in files.items():
            (d / f).write_text(c)
        skills = d / ".openhands" / "skills"
        skills.mkdir(parents=True, exist_ok=True)
        (skills / f"{name}-notes.md").write_text(
            f"---\nname: {name}-notes\ntriggers:\n- {name}deploy\n---\n"
            f"How {name} deploys.\n"
        )
        for cmd in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "add", "-A"],
            ["git", "commit", "-qm", "init"],
            ["git", "branch", "release"],
        ):
            subprocess.run(cmd, cwd=d, check=True, env=git_env())
    ws = RW(host=server.base_url, working_dir=str(remote_dir), api_key=KEY)
    target = remote_dir / "repos"
    res = ws.clone_repos(
        [
            f"file://{origin / 'alpha'}",
            {"url": f"file://{origin / 'beta'}", "ref": "release"},
            RepoSource(url=f"file://{origin / 'gamma'}"),
        ],
        target_dir=target,
    )
    result["clone"] = {
        "success_count": res.success_count,
        "failed": res.failed_repos,
        "dirs": sorted(Path(m.local_path).name for m in res.repo_mappings.values()),
    }
    result["cloned_files_exist"] = all(
        (Path(m.local_path) / "README.md").exists() for m in res.repo_mappings.values()
    )
    try:
        RepoSource.model_validate({"url": "owner/repo"})
        result["short_url_without_provider"] = "accepted"
    except Exception:
        result["short_url_without_provider"] = "rejected"
    result["short_url_with_provider"] = (
        RepoSource.model_validate({"url": "owner/repo", "provider": "github"})
        .get_provider()
        .value
    )
    context = ws.get_repos_context(res.repo_mappings)
    result["context_mentions"] = [n for n in ("alpha", "beta", "gamma") if n in context]
    result["empty_context"] = ws.get_repos_context({})

    dirs = sorted(m.local_path for m in res.repo_mappings.values())
    skills, ctx = ws.load_skills_from_agent_server(
        project_dirs=dirs, load_public=False, load_user=False, load_org=False
    )
    result["skills"] = sorted(s.name for s in skills)
    result["context_skills"] = sorted(s.name for s in ctx.skills)
    result["context_load_public"] = ctx.load_public_skills
    none, ctx2 = ws.load_skills_from_agent_server(
        project_dirs=[str(run / "no-skills-here")],
        load_public=False,
        load_user=False,
        load_project=True,
        load_org=False,
    )
    result["fallback_skills"] = len(none)
    result["fallback_load_public"] = ctx2.load_public_skills


with AgentServer(run / "agent-server", env=server_env) as server:
    {"ops": ops, "settings": settings, "conversation": conversation, "repos": repos}[
        mode
    ](server)
emit(result)
