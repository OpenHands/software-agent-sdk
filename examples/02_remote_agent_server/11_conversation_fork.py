"""Fork a conversation through the agent server REST API.

Demonstrates ``RemoteConversation.fork()`` which delegates to the server's
``POST /api/conversations/{id}/fork`` endpoint.  The fork deep-copies
events and state on the server side, then returns a new
``RemoteConversation`` pointing at the copy.

Scenarios covered:
  1. Run a source conversation on the server
  2. Fork it — verify independent event histories
  3. Fork with a title and custom tags
"""

import os
import subprocess
import sys
import tempfile
import threading
import time

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, RemoteConversation, Tool, Workspace
from openhands.tools.terminal import TerminalTool


# -----------------------------------------------------------------
# Managed server helper (reused from example 01)
# -----------------------------------------------------------------
def _stream_output(stream, prefix, target_stream):
    try:
        for line in iter(stream.readline, ""):
            if line:
                target_stream.write(f"[{prefix}] {line}")
                target_stream.flush()
    except Exception as e:
        print(f"Error streaming {prefix}: {e}", file=sys.stderr)
    finally:
        stream.close()


class ManagedAPIServer:
    """Context manager that starts and stops a local agent-server."""

    def __init__(self, port: int = 8000, host: str = "127.0.0.1"):
        self.port = port
        self.host = host
        self.process: subprocess.Popen[str] | None = None
        self.base_url = f"http://{host}:{port}"

    def __enter__(self):
        print(f"Starting agent-server on {self.base_url} ...")
        self.process = subprocess.Popen(
            [
                "python",
                "-m",
                "openhands.agent_server",
                "--port",
                str(self.port),
                "--host",
                self.host,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={"LOG_JSON": "true", **os.environ},
        )
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        threading.Thread(
            target=_stream_output,
            args=(self.process.stdout, "SERVER", sys.stdout),
            daemon=True,
        ).start()
        threading.Thread(
            target=_stream_output,
            args=(self.process.stderr, "SERVER", sys.stderr),
            daemon=True,
        ).start()

        import httpx

        for _ in range(30):
            try:
                if httpx.get(f"{self.base_url}/health", timeout=1.0).status_code == 200:
                    print(f"Agent-server ready at {self.base_url}")
                    return self
            except Exception:
                pass
            assert self.process.poll() is None, "Server exited unexpectedly"
            time.sleep(1)
        raise RuntimeError("Server failed to start in 30 s")

    def __exit__(self, *args):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            time.sleep(0.5)
            print("Agent-server stopped.")


# -----------------------------------------------------------------
# Config
# -----------------------------------------------------------------
api_key = os.getenv("LLM_API_KEY")
assert api_key, "LLM_API_KEY must be set"

llm = LLM(
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    api_key=SecretStr(api_key),
    base_url=os.getenv("LLM_BASE_URL"),
)
agent = Agent(llm=llm, tools=[Tool(name=TerminalTool.name)])

# -----------------------------------------------------------------
# Run
# -----------------------------------------------------------------
with ManagedAPIServer(port=8002) as server:
    workspace_dir = tempfile.mkdtemp(prefix="fork_demo_")
    workspace = Workspace(host=server.base_url, working_dir=workspace_dir)

    # =============================================================
    # 1. Source conversation
    # =============================================================
    source = Conversation(agent=agent, workspace=workspace)
    assert isinstance(source, RemoteConversation)

    source.send_message("Run `echo hello-from-source` in the terminal.")
    source.run()

    print("=" * 64)
    print("  RemoteConversation.fork() — Agent-Server Example")
    print("=" * 64)
    print(f"\nSource conversation ID : {source.id}")
    source_event_count = len(source.state.events)
    print(f"Source events count    : {source_event_count}")

    # =============================================================
    # 2. Fork and continue independently
    # =============================================================
    fork = source.fork(title="Follow-up fork")
    assert isinstance(fork, RemoteConversation)

    print("\n--- Fork created ---")
    print(f"Fork ID                : {fork.id}")
    print(f"Fork events (copied)   : {len(fork.state.events)}")

    assert fork.id != source.id
    assert len(fork.state.events) == source_event_count

    fork.send_message("Now run `echo hello-from-fork` in the terminal.")
    fork.run()

    # Source must be untouched
    assert len(source.state.events) == source_event_count
    print("\n--- After running fork ---")
    print(f"Source events (unchanged): {source_event_count}")
    print(f"Fork events (grew)       : {len(fork.state.events)}")

    # =============================================================
    # 3. Fork with tags
    # =============================================================
    fork_tagged = source.fork(
        title="Tagged experiment",
        tags={"purpose": "a/b-test"},
    )
    assert isinstance(fork_tagged, RemoteConversation)

    print("\n--- Fork with tags ---")
    print(f"Fork ID     : {fork_tagged.id}")

    fork_tagged.send_message(
        "What command did you run earlier? Just tell me, no tools."
    )
    fork_tagged.run()

    print(f"Fork events : {len(fork_tagged.state.events)}")

    # =============================================================
    # Summary
    # =============================================================
    print(f"\n{'=' * 64}")
    print("All done — RemoteConversation.fork() works end-to-end.")
    print("=" * 64)

    # Cleanup
    fork.close()
    fork_tagged.close()
    source.close()

cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
