"""Execute tools directly on a conversation via the agent server.

Demonstrates ``RemoteConversation.execute_tool()`` which calls the server's
``POST /api/conversations/{id}/execute_tool`` endpoint.  This lets you run
tools (like the terminal) on a conversation **without** going through the
agent loop — useful for pre-run setup where environment changes must persist
in the agent's session (e.g. running ``.openhands/setup.sh``).

The example shows two approaches:
  1. **SDK** — ``RemoteConversation.execute_tool(action)``
  2. **Raw HTTP** — ``POST /api/conversations/{id}/execute_tool`` via httpx

Both produce the same result.  Approach 1 is the recommended way when you
already have a ``RemoteConversation`` object; approach 2 is useful when
integrating from a language or service that doesn't use the SDK.
"""

import os
import subprocess
import sys
import tempfile
import threading
import time

import httpx
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, RemoteConversation, Tool, Workspace
from openhands.tools.terminal import TerminalTool
from openhands.tools.terminal.definition import TerminalAction


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

        for _ in range(30):
            try:
                r = httpx.get(f"{self.base_url}/health", timeout=1.0)
                if r.status_code == 200:
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
with ManagedAPIServer(port=8003) as server:
    workspace_dir = tempfile.mkdtemp(prefix="execute_tool_demo_")
    workspace = Workspace(host=server.base_url, working_dir=workspace_dir)

    conversation = Conversation(agent=agent, workspace=workspace)
    assert isinstance(conversation, RemoteConversation)

    print("=" * 64)
    print("  execute_tool — Direct Tool Execution on Agent Server")
    print("=" * 64)
    print(f"\nConversation ID: {conversation.id}")

    # =============================================================
    # Approach 1: SDK — RemoteConversation.execute_tool()
    # =============================================================
    print("\n--- Approach 1: SDK (RemoteConversation.execute_tool) ---")

    # Run a command that sets an environment variable
    obs1 = conversation.execute_tool(
        "terminal",
        TerminalAction(command="export MY_SETUP_VAR='hello from setup'"),
    )
    print(f"Set env var  → is_error={obs1.is_error}")

    # Verify the variable persists in the same terminal session
    obs2 = conversation.execute_tool(
        "terminal",
        TerminalAction(command="echo $MY_SETUP_VAR"),
    )
    print(f"Read env var → text={obs2.text!r}")
    assert "hello from setup" in obs2.text, (
        f"Environment variable did not persist! Got: {obs2.text!r}"
    )
    print("✅ Environment variable persisted across execute_tool calls!")

    # =============================================================
    # Approach 2: Raw HTTP — POST /api/conversations/{id}/execute_tool
    # =============================================================
    print("\n--- Approach 2: Raw HTTP (httpx) ---")

    conv_id = str(conversation.id)
    url = f"{server.base_url}/api/conversations/{conv_id}/execute_tool"

    # Set another variable via raw HTTP
    resp = httpx.post(
        url,
        json={
            "tool_name": "terminal",
            "action": {
                "command": "export HTTP_VAR='set via raw HTTP'",
            },
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    result = resp.json()
    print(f"Set env var  → is_error={result['is_error']}")

    # Read both variables to show everything shares the same session
    resp = httpx.post(
        url,
        json={
            "tool_name": "terminal",
            "action": {
                "command": "echo SDK=$MY_SETUP_VAR HTTP=$HTTP_VAR",
            },
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    result = resp.json()

    # Extract text from observation content
    obs_data = result["observation"]
    content = obs_data.get("content", [])
    text = content[0]["text"] if content else ""
    print(f"Read both    → text={text!r}")

    assert "hello from setup" in text, f"SDK var missing! Got: {text!r}"
    assert "set via raw HTTP" in text, f"HTTP var missing! Got: {text!r}"
    print("✅ Both variables persisted — same terminal session!")

    # =============================================================
    # Bonus: the agent loop sees these changes too
    # =============================================================
    print("\n--- Verify agent session shares the environment ---")
    conversation.send_message(
        "Run `echo MY_SETUP_VAR=$MY_SETUP_VAR` in the terminal. "
        "Report the value you see."
    )
    conversation.run()
    print("✅ Agent ran in the same session with pre-configured env.")

    # =============================================================
    # Summary
    # =============================================================
    print(f"\n{'=' * 64}")
    print("All done — execute_tool works via SDK and raw HTTP.")
    print("=" * 64)

    conversation.close()

cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
