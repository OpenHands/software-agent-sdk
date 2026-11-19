"""pause() while run() is still making the agent ready (issue #6848).

A conversation restored from `persistence_dir` makes its agent ready on the
first `run()`: here that starts a stdio MCP server (`S09/mcp_server.py`) that
takes 2 s to come up. `run()` goes to a thread, `pause()` follows 0.5 s later,
while the server is still starting. The model is a scripted `TestLLM` with one
`finish` step; the program reports the status around the pause, the LLM calls
made after it, and what a later `run()` does.

Usage: pause_during_startup.py
"""

import sys
import threading
import time
import uuid
from pathlib import Path

from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, Conversation
from openhands.sdk.event import PauseEvent
from openhands.sdk.mcp import MCPServer


paths = run_paths()
SERVER = str(Path(__file__).parent.parent / "S09" / "mcp_server.py")
START_SECONDS = 2.0
PAUSE_AFTER_SECONDS = 0.5
cid = uuid.uuid4()


def open_conversation(llm, mcp_config):
    agent = Agent(llm=llm, tools=[], mcp_config=mcp_config)
    return Conversation(
        agent=agent,
        workspace=str(paths["workspace"]),
        persistence_dir=str(paths["conversations"]),
        conversation_id=cid,
        visualizer=None,
        delete_on_close=False,
    )


# The first conversation only stores the message; it has no MCP server, so
# only the restored one waits for a server to start.
first = open_conversation(make_llm(script=[]), {})
first.send_message("Say done.")
first.close()

llm = make_llm(script=[{"finish": "done"}])
slow_server = MCPServer(
    command=sys.executable,
    args=[SERVER, "stdio", "--slow-start", str(START_SECONDS)],
)
conversation = open_conversation(llm, {"slow": slow_server})
out = {"ok": True, "status_after_restore": conversation.state.execution_status.value}

runner = threading.Thread(target=conversation.run)
started = time.monotonic()
runner.start()
time.sleep(PAUSE_AFTER_SECONDS)
# Holding the state's (reentrant, FIFO) lock across pause() reads the status
# pause() left before the run thread can take the lock back.
with conversation.state:
    conversation.pause()
    calls_at_pause = llm.call_count
    out["pause_returned_after_s"] = round(time.monotonic() - started, 2)
    out["status_after_pause"] = conversation.state.execution_status.value
runner.join(timeout=60)
out |= {
    "run_returned_after_s": round(time.monotonic() - started, 2),
    "status_after_run": conversation.state.execution_status.value,
    "llm_calls_after_pause": llm.call_count - calls_at_pause,
    "pause_events": sum(isinstance(e, PauseEvent) for e in conversation.state.events),
}

conversation.run()
out |= {
    "status_after_resume": conversation.state.execution_status.value,
    "llm_calls_total": llm.call_count,
    "script_left": llm.remaining_responses,
}
conversation.close()
emit(out)
