"""Use a local workspace the way a program does: build it through the
`Workspace` factory, run commands, copy files, read git state, pause and
resume, and run a scripted conversation in it with the automation completion
callback pointed at a local capture server.
"""

import os
import subprocess
from pathlib import Path

from sdk_verify import emit, make_llm, run_paths
from servers import CaptureServer

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.workspace import (
    CommandResult,
    FileOperationResult,
    LocalWorkspace,
    Workspace,
)
from openhands.tools.terminal import TerminalTool


root = run_paths()["workspace"] / "local-ws"
root.mkdir(parents=True, exist_ok=True)

from_str = Workspace(working_dir=str(root))
from_path = Workspace(working_dir=root)

ws = from_path
ok = ws.execute_command("echo hi && pwd")
failing = ws.execute_command("echo oops >&2; exit 3")
in_sub = ws.execute_command("pwd", cwd="/tmp")
slow = ws.execute_command("sleep 5", timeout=1)

(root / "a.txt").write_text("payload\n")
up = ws.file_upload(root / "a.txt", root / "deep" / "dir" / "b.txt")
down = ws.file_download(
    str(root / "deep" / "dir" / "b.txt"), str(root / "out" / "c.txt")
)
missing = ws.file_download(root / "nope.txt", root / "out" / "d.txt")

# A git repository with one committed file, then one change.
repo = root / "repo"
repo.mkdir(exist_ok=True)
git_env = {
    **os.environ,
    "GIT_AUTHOR_NAME": "v",
    "GIT_AUTHOR_EMAIL": "v@example.com",
    "GIT_COMMITTER_NAME": "v",
    "GIT_COMMITTER_EMAIL": "v@example.com",
}
for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"]):
    subprocess.run(cmd, cwd=repo, check=True, env=git_env)
(repo / "f.txt").write_text("one\n")
subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=git_env)
subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, env=git_env)
(repo / "f.txt").write_text("one\ntwo\n")
(repo / "new.txt").write_text("new\n")
changes = ws.git_changes("repo")
diff = ws.git_diff("repo/f.txt")

pause_error = None
try:
    ws.pause()
    ws.resume()
except Exception as exc:
    pause_error = repr(exc)

# A scripted conversation inside the workspace context, as an automation
# runner would drive it; on exit the workspace POSTs its completion callback.
with CaptureServer() as callback:
    os.environ["AUTOMATION_CALLBACK_URL"] = f"{callback.base_url}/callback"
    os.environ["AUTOMATION_CALLBACK_API_KEY"] = "cb-token"
    os.environ["AUTOMATION_RUN_ID"] = "run-42"
    with LocalWorkspace(working_dir=root) as cws:
        agent = Agent(
            llm=make_llm(
                [
                    {
                        "tool": "terminal",
                        "args": {"command": "echo from-agent > agent.txt"},
                    },
                    {"finish": "wrote agent.txt"},
                ]
            ),
            tools=[Tool(name=TerminalTool.name)],
        )
        conversation = Conversation(agent=agent, workspace=cws, visualizer=None)
        conversation_type = type(conversation).__name__
        conversation.send_message("write agent.txt")
        conversation.run()
        conversation.close()
    agent_file = (
        (root / "agent.txt").read_text().strip()
        if (root / "agent.txt").exists()
        else None
    )
    ok_posts = list(callback.requests)

    # Failure path: an exception inside the context is reported as FAILED.
    try:
        with LocalWorkspace(working_dir=root):
            raise RuntimeError("boom in automation")
    except RuntimeError:
        pass
    failed_posts = callback.requests[len(ok_posts) :]
    for name in (
        "AUTOMATION_CALLBACK_URL",
        "AUTOMATION_CALLBACK_API_KEY",
        "AUTOMATION_RUN_ID",
    ):
        os.environ.pop(name)
    # Without the URL nothing is sent.
    seen = len(callback.requests)
    with LocalWorkspace(working_dir=root):
        pass
    posts_after_unset = len(callback.requests) - seen

# An unreachable URL does not raise.
os.environ["AUTOMATION_CALLBACK_URL"] = "http://127.0.0.1:9/unreachable"
unreachable_error = None
try:
    with LocalWorkspace(working_dir=root):
        pass
except Exception as exc:
    unreachable_error = repr(exc)
os.environ.pop("AUTOMATION_CALLBACK_URL")

first = ok_posts[0] if ok_posts else {}
failed = failed_posts[0] if failed_posts else {}
emit(
    {
        "ok": True,
        "factory_type": type(from_str).__name__,
        "factory_path_type": type(from_path).__name__,
        "working_dir_is_str": isinstance(from_path.working_dir, str),
        "command_result_type": type(ok).__name__
        if isinstance(ok, CommandResult)
        else None,
        "exit_code": ok.exit_code,
        "stdout_lines": ok.stdout.split(),
        "pwd_is_root": ok.stdout.split()[-1] == str(root) if ok.stdout else False,
        "failing_exit": failing.exit_code,
        "failing_stderr": failing.stderr.strip(),
        "cwd_override": in_sub.stdout.strip(),
        "timeout_occurred": slow.timeout_occurred,
        "upload_ok": isinstance(up, FileOperationResult) and up.success,
        "upload_size": up.file_size,
        "upload_created_parents": (root / "deep" / "dir" / "b.txt").exists(),
        "download_ok": down.success,
        "download_content": (root / "out" / "c.txt").read_text().strip(),
        "missing_download_success": missing.success,
        "missing_download_error": bool(missing.error),
        "git_changes": sorted(f"{c.status.value}:{Path(c.path).name}" for c in changes),
        "git_diff_original": diff.original,
        "git_diff_modified": diff.modified,
        "pause_resume_error": pause_error,
        "conversation_type": conversation_type,
        "agent_file": agent_file,
        "callback_posts": len(ok_posts),
        "callback_status": (first.get("body") or {}).get("status"),
        "callback_run_id": (first.get("body") or {}).get("run_id"),
        "callback_has_cost": "cost" in (first.get("body") or {}),
        "callback_auth": first.get("headers", {}).get("authorization"),
        "failed_status": (failed.get("body") or {}).get("status"),
        "failed_error_code": ((failed.get("body") or {}).get("error") or {}).get(
            "code"
        ),
        "posts_after_unset": posts_after_unset,
        "unreachable_error": unreachable_error,
    }
)
