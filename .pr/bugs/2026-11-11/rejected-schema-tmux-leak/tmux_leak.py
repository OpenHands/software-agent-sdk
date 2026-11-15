"""Does a rejected TerminalTool response_schema leave a tmux server after close()?

Usage: TMUX_TMPDIR=<fresh dir> python tmux_leak.py reserved|valid
"""
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

from pydantic import BaseModel

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import register_tool
from openhands.tools.terminal import TerminalTool


class Reserved(BaseModel):
    summary: str


class Valid(BaseModel):
    verdict: str


def live_servers() -> list[str]:
    d = Path(os.environ["TMUX_TMPDIR"]) / f"tmux-{os.getuid()}"
    alive = []
    for sock in sorted(d.glob("*")) if d.exists() else []:
        r = subprocess.run(["tmux", "-L", sock.name, "ls"], capture_output=True, text=True)
        print(f"  socket {sock.name}: rc={r.returncode} {(r.stdout or r.stderr).strip()[:120]}")
        if r.returncode == 0:
            alive.append(sock.name)
    return alive


def main(mode: str) -> None:
    schema = Reserved if mode == "reserved" else Valid
    register_tool(TerminalTool.name, TerminalTool)
    llm = TestLLM.from_messages([]) if hasattr(TestLLM, "from_messages") else TestLLM()
    agent = Agent(llm=llm, tools=[Tool(name=TerminalTool.name, params={"response_schema": schema})])
    ws = tempfile.mkdtemp(prefix="tmuxleak-ws-")
    conv = Conversation(agent=agent, workspace=ws, visualizer=None)
    try:
        conv.send_message("x")
        conv.run()
        print("run: returned normally")
    except Exception as e:  # noqa: BLE001
        print(f"run: raised {type(e).__name__}: {e}")
        tb = traceback.format_exc()
        for marker in ("set_response_schema", "resolve_tool"):
            print(f"  traceback mentions {marker}: {marker in tb}")
    finally:
        conv.close()
        print("close: done")
    alive = live_servers()
    print(f"RESULT mode={mode} tmux_servers_alive_after_close={len(alive)} {alive}")


if __name__ == "__main__":
    main(sys.argv[1])
