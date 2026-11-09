"""Drive the terminal tool through a scripted agent, the way a program
configures it: `Tool(name="terminal", params={...})` with a backend, a short
no-change timeout and client env, plus a conversation secret.

Usage: terminal.py auto|tmux|subprocess

Reports each named step's observation (exit code, cwd, error flag, text the
model sees) and what is left running after `conversation.close()`.
"""

import os
import re
import socket
import subprocess
import sys
from pathlib import Path

from S14.toolkit import converse, tool_results, workspace
from sdk_verify import emit

from openhands.sdk import Tool
from openhands.tools.terminal import TerminalTool


mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
params: dict = {"no_change_timeout_seconds": 3, "env": {"CLIENT_VAR": "from-client"}}
if mode != "auto":
    params["terminal_type"] = mode
if mode == "subprocess":
    # shell_path picks the shell binary: a bash under another name shows it is used.
    shell = workspace().parent / "home" / "my-shell"
    shell.unlink(missing_ok=True)
    shell.symlink_to("/bin/bash")
    params["shell_path"] = str(shell)
os.environ["PARENT_VAR"] = "inherited"  # the program's own environment

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]

heredoc = (
    "cat > big.txt <<'EOF'\n" + "\n".join(f"line {i}" for i in range(400)) + "\nEOF"
)
steps = [
    ("cd", {"command": "mkdir -p sub && cd sub && export FOO=bar"}),
    ("shell", {"command": "echo shell=$0"}),
    (
        "persist",
        {"command": "pwd && echo FOO=$FOO CLIENT=$CLIENT_VAR PARENT=$PARENT_VAR"},
    ),
    ("fail", {"command": "ls /no-such-dir"}),
    ("two_commands", {"command": "echo one\necho two"}),
    ("heredoc", {"command": heredoc}),
    ("count", {"command": "wc -l < big.txt"}),
    (
        "server",
        {
            "command": f"nohup python3 -m http.server {port} --bind 127.0.0.1 "
            "> server.log 2>&1 &"
        },
    ),
    (
        "server_up",
        {
            "command": "sleep 1 && python3 -c "
            '"import urllib.request as u; '
            f"print('status', u.urlopen('http://127.0.0.1:{port}/').status)\""
        },
    ),
    ("server_stop", {"command": f"pkill -f 'http.server {port}'; echo stopped"}),
    ("prompt", {"command": "python3 -c \"x = input('name? '); print('hello', x)\""}),
    ("answer", {"command": "bob", "is_input": True}),
    ("idle", {"command": "sleep 30"}),
    ("more", {"command": ""}),
    ("ctrl_c", {"command": "C-c", "is_input": True}),
    ("hard", {"command": "sleep 5", "timeout": 1}),
    ("after_hard", {"command": "C-c", "is_input": True}),
    ("capped", {"command": "echo capped-ran", "timeout": 95}),
    ("big", {"command": "python3 -c \"print('x' * 40000)\""}),
    ("dsr", {"command": "printf 'a\\033[6nb\\n'"}),
    ("after_dsr", {"command": "echo next"}),
    (
        "fake_ps1",
        {"command": "printf '\\n###PS1JSON###\\n{broken\\n' ; sh -c 'exit 3'"},
    ),
    ("literal", {"command": '["ls", "-la"]'}),
    ("secret", {"command": "echo token=$MY_TOKEN"}),
    ("reset", {"command": "", "reset": True}),
    ("after_reset", {"command": "pwd && echo FOO=$FOO"}),
    ("shell_after_reset", {"command": "echo shell=$0"}),
    ("reset_input", {"command": "x", "reset": True, "is_input": True}),
]
# Three terminal calls in one LLM response; each prints when it started and ended.
timed = "echo start $(date +%s.%N); sleep 2; echo end $(date +%s.%N)"
script = [{"tool": "terminal", "args": args} for _, args in steps] + [
    {"tools": [{"tool": "terminal", "args": {"command": timed}} for _ in range(3)]},
    {"finish": "done"},
]

conversation = converse(
    [Tool(name=TerminalTool.name, params=params)],
    script,
    secrets={"MY_TOKEN": "s3cr3t-value-123"},
    agent_kwargs={"tool_concurrency_limit": 4},
)
full_rows = tool_results(conversation, clip=10**6)
rows = tool_results(conversation)
big_full = [r for r in full_rows if r["tool"] == "terminal"][
    [n for n, _ in steps].index("big")
]["text"]
terminal_rows = [r for r in rows if r["tool"] == "terminal"]
named = dict(zip([n for n, _ in steps], terminal_rows))
parallel = terminal_rows[len(steps) :]
starts = [
    float(m) for r in parallel for m in re.findall(r"start (\d+\.\d+)", r["text"])
]
ends = [float(m) for r in parallel for m in re.findall(r"end (\d+\.\d+)", r["text"])]
texts = "".join(r["text"] for r in rows)
executor = conversation.agent.tools_map["terminal"].executor
pooled = bool(getattr(executor, "is_pooled", False))
saved_dir = conversation.state.env_observation_persistence_dir
saved = sorted(p.name for p in Path(saved_dir).glob("*")) if saved_dir else []
ws = str(workspace())


def shell_alive() -> bool:
    """The backend's shell: tmux servers for the pane pool, else the process."""
    if pooled or not hasattr(executor.session.terminal, "process"):
        return live_tmux_servers() > 0
    process = executor.session.terminal.process
    return process is not None and process.poll() is None


def live_tmux_servers() -> int:
    root = Path(os.environ.get("TMUX_TMPDIR", "/tmp")) / f"tmux-{os.getuid()}"
    alive = 0
    for sock in root.glob("openhands*"):
        probe = subprocess.run(["tmux", "-S", str(sock), "ls"], capture_output=True)
        alive += probe.returncode == 0
    return alive


alive_before = shell_alive()
executor_process = (
    getattr(executor.session.terminal, "process", None) if not pooled else None
)
conversation.close()

emit(
    {
        "ok": True,
        "mode": mode,
        "pooled": pooled,
        "backend_at_end": type(
            executor._pool if pooled else executor.session.terminal
        ).__name__,
        "steps": named,
        "steps_run": len(named),
        "cwd_is_sub": (named["persist"].get("cwd") or "").endswith("/sub"),
        "after_reset_in_workspace": named["after_reset"].get("cwd") == ws,
        "heredoc_lines": len(
            (workspace() / "sub" / "big.txt").read_text().splitlines()
        ),
        "escape_filtered": "\x1b[6n" not in named["dsr"]["text"]
        and "ab" in named["dsr"]["text"],
        "dsr_reply_visible": bool(
            re.search(
                r"\[\d+;\d+R|;\d+R", named["dsr"]["text"] + named["after_dsr"]["text"]
            )
        ),
        "after_dsr_clean": "syntax error" not in named["after_dsr"]["text"],
        "parallel_calls": len(parallel),
        "parallel_overlap": len(starts) == 3
        and len(ends) == 3
        and max(starts) < min(ends),
        "secret_leaked": "s3cr3t-value-123" in str(rows),
        "big_saved_files": [n for n in saved if "terminal" in n],
        "big_text_len": named["big"]["text_len"],
        "big_notice_names_saved_file": bool(saved)
        and "complete output has been saved to" in big_full
        and any(n in big_full for n in saved),
        "bracketed_paste_in_output": "\x1b[?2004" in texts,
        "shell_alive_before_close": alive_before,
        "shell_alive_after_close": (
            executor_process.poll() is None
            if executor_process
            else live_tmux_servers() > 0
        ),
        "tmux_servers_after_close": live_tmux_servers(),
    }
)
