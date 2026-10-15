"""Stdio MCP server that publishes the processes a cleanup test should observe.

The server writes its own pid, and can leave a child that ignores SIGTERM in
the same process group. Tests read those pid files instead of client internals.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

from fastmcp import FastMCP


mcp = FastMCP("stdio-cleanup-probe")

_CHILD_SCRIPT = (
    "import os, signal, time\n"
    "from pathlib import Path\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "Path(os.environ['OH_MCP_CHILD_READY']).write_text('ready')\n"
    "time.sleep(120)\n"
)


def _spawn_child() -> None:
    """Start a descendant and publish its pid once it is ignoring SIGTERM."""
    ready = Path(os.environ["OH_MCP_CHILD_READY"])
    ready.unlink(missing_ok=True)
    child = subprocess.Popen([sys.executable, "-c", _CHILD_SCRIPT])
    deadline = time.monotonic() + 5
    while not ready.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("cleanup probe child did not start")
        time.sleep(0.01)
    Path(os.environ["OH_MCP_CHILD_PIDFILE"]).write_text(str(child.pid))


@mcp.tool()
def release() -> str:
    """Exit this process and leave its child running."""
    _spawn_child()
    os._exit(0)


if __name__ == "__main__":
    Path(os.environ["OH_MCP_PIDFILE"]).write_text(str(os.getpid()))
    if os.environ.get("OH_MCP_SPAWN_CHILD") == "1":
        _spawn_child()
    mcp.run(transport="stdio", show_banner=False)
