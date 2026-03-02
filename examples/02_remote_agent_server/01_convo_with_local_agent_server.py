import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation, RemoteConversation, Workspace, get_logger
from openhands.sdk.event import ConversationStateUpdateEvent, HookExecutionEvent
from openhands.sdk.hooks import HookConfig, HookDefinition, HookMatcher
from openhands.tools.preset.default import get_default_agent


logger = get_logger(__name__)

# Hook script directory for this example
HOOK_SCRIPTS_DIR = Path(__file__).parent / "hook_scripts"


def _stream_output(stream, prefix, target_stream):
    """Stream output from subprocess to target stream with prefix."""
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
    """Context manager for subprocess-managed OpenHands API server."""

    def __init__(self, port: int = 8000, host: str = "127.0.0.1"):
        self.port: int = port
        self.host: str = host
        self.process: subprocess.Popen[str] | None = None
        self.base_url: str = f"http://{host}:{port}"
        self.stdout_thread: threading.Thread | None = None
        self.stderr_thread: threading.Thread | None = None

    def __enter__(self):
        """Start the API server subprocess."""
        print(f"Starting OpenHands API server on {self.base_url}...")

        # Start the server process
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

        # Start threads to stream stdout and stderr
        assert self.process is not None
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        self.stdout_thread = threading.Thread(
            target=_stream_output,
            args=(self.process.stdout, "SERVER", sys.stdout),
            daemon=True,
        )
        self.stderr_thread = threading.Thread(
            target=_stream_output,
            args=(self.process.stderr, "SERVER", sys.stderr),
            daemon=True,
        )

        self.stdout_thread.start()
        self.stderr_thread.start()

        # Wait for server to be ready
        max_retries = 30
        for i in range(max_retries):
            try:
                import httpx

                response = httpx.get(f"{self.base_url}/health", timeout=1.0)
                if response.status_code == 200:
                    print(f"API server is ready at {self.base_url}")
                    return self
            except Exception:
                pass

            assert self.process is not None
            if self.process.poll() is not None:
                # Process has terminated
                raise RuntimeError(
                    "Server process terminated unexpectedly. "
                    "Check the server logs above for details."
                )

            time.sleep(1)

        raise RuntimeError(f"Server failed to start after {max_retries} seconds")

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop the API server subprocess."""
        if self.process:
            print("Stopping API server...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print("Force killing API server...")
                self.process.kill()
                self.process.wait()

            # Wait for streaming threads to finish (they're daemon threads,
            # so they'll stop automatically)
            # But give them a moment to flush any remaining output
            time.sleep(0.5)
            print("API server stopped.")


api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."

llm = LLM(
    usage_id="agent",
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=SecretStr(api_key),
)
title_gen_llm = LLM(
    usage_id="title-gen-llm",
    model=os.getenv("LLM_MODEL", "openhands/gpt-5-mini-2025-08-07"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=SecretStr(api_key),
)

# Use managed API server
with ManagedAPIServer(port=8001) as server:
    # Create agent
    agent = get_default_agent(
        llm=llm,
        cli_mode=True,  # Disable browser tools for simplicity
    )

    # Define callbacks to test the WebSocket functionality
    received_events = []
    event_tracker = {"last_event_time": time.time()}

    def event_callback(event):
        """Callback to capture events for testing."""
        event_type = type(event).__name__
        logger.info(f"🔔 Callback received event: {event_type}\n{event}")
        received_events.append(event)
        event_tracker["last_event_time"] = time.time()

    # Create RemoteConversation with callbacks
    # NOTE: Workspace is required for RemoteConversation
    # Use a temp directory that exists and is accessible in CI environments
    temp_workspace_dir = tempfile.mkdtemp(prefix="agent_server_demo_")
    workspace = Workspace(host=server.base_url, working_dir=temp_workspace_dir)
    result = workspace.execute_command("pwd")
    logger.info(
        f"Command '{result.command}' completed with exit code {result.exit_code}"
    )
    logger.info(f"Output: {result.stdout}")

    # Configure hooks - demonstrating the hooks system with RemoteConversation
    # Server-side hooks (PreToolUse, PostToolUse, UserPromptSubmit, Stop) are
    # executed by the agent server. Client-side hooks (SessionStart, SessionEnd)
    # are executed locally.

    hook_config = HookConfig(
        # Stop hook - run Python syntax check before allowing agent to finish.
        # If any Python file has syntax errors, the hook returns "deny" with the
        # error output, which gets sent back to the agent as feedback, and the
        # agent continues working to fix the issue.
        stop=[
            HookMatcher(
                matcher="*",  # Match all stop reasons
                hooks=[
                    HookDefinition(
                        command=str(HOOK_SCRIPTS_DIR / "pre_commit_check.sh"),
                        timeout=60,
                    )
                ],
            )
        ],
    )

    conversation = Conversation(
        agent=agent,
        workspace=workspace,
        callbacks=[event_callback],
        hook_config=hook_config,
    )
    assert isinstance(conversation, RemoteConversation)

    # Track hook execution events
    hook_events: list[HookExecutionEvent] = []

    def hook_event_tracker(event):
        """Additional callback to track hook execution events."""
        if isinstance(event, HookExecutionEvent):
            hook_events.append(event)
            logger.info(f"🪝 HookExecutionEvent captured: {event.hook_event_type}")

    # Append our hook tracker to the existing callbacks
    conversation._callbacks.append(hook_event_tracker)

    try:
        logger.info(f"\n📋 Conversation ID: {conversation.state.id}")

        # Test scenario: Ask the agent to create a Python file with syntax errors
        # The stop hook should detect the syntax error and send feedback back
        # to the agent to fix it
        logger.info("📝 Sending message to test on_stop hook with syntax check...")
        conversation.send_message(
            "Create a Python file called 'test_broken.py' in the current directory "
            "with an obvious syntax error (like 'def broken(:\n    pass' - missing "
            "closing parenthesis). After creating the file, immediately use the "
            "finish action. If you receive any feedback about errors, fix them and "
            "try to finish again."
        )

        # Generate title using a specific LLM
        title = conversation.generate_title(max_length=60, llm=title_gen_llm)
        logger.info(f"Generated conversation title: {title}")

        logger.info("🚀 Running conversation...")
        logger.info(
            "Expected behavior: Agent creates broken .py file -> tries to finish "
            "-> stop hook runs syntax check -> check fails -> hook sends feedback "
            "-> agent fixes the syntax error -> tries to finish again -> passes"
        )

        # Keep running until the agent actually finishes
        # When a stop hook denies, the state goes: running -> finished -> running
        # The client's run() may return when it sees 'finished', so we need to
        # check if the agent is still running and continue
        max_runs = 10  # Allow enough retries for agent to fix issues
        run_count = 0
        while run_count < max_runs:
            run_count += 1
            logger.info(f"🔄 Run attempt #{run_count}")
            conversation.run()
            current_status = conversation.state.execution_status
            logger.info(f"   After run(), status = {current_status}")

            # Small delay to let any pending state updates arrive
            time.sleep(0.5)
            current_status = conversation.state.execution_status
            logger.info(f"   After delay, status = {current_status}")

            if current_status.value == "finished":
                logger.info("   ✅ Agent finished!")
                break
            elif current_status.value == "running":
                logger.info("   Agent still running (hook denied stop), continuing...")
            else:
                logger.info(f"   Unexpected status: {current_status}, stopping")
                break

        logger.info("✅ Task completed!")
        logger.info(f"Final agent status: {conversation.state.execution_status}")

        # Wait for events to stop coming (no events for 2 seconds)
        logger.info("⏳ Waiting for events to stop...")
        while time.time() - event_tracker["last_event_time"] < 2.0:
            time.sleep(0.1)
        logger.info("✅ Events have stopped")

        # Analyze hook execution events
        logger.info("\n" + "=" * 50)
        logger.info("📊 Hook Execution Events Analysis")
        logger.info("=" * 50)

        logger.info(f"Total HookExecutionEvents received: {len(hook_events)}")
        for i, he in enumerate(hook_events, 1):
            logger.info(f"\n  Hook Event #{i}:")
            logger.info(f"    Type: {he.hook_event_type}")
            logger.info(f"    Command: {he.hook_command}")
            logger.info(f"    Success: {he.success}")
            logger.info(f"    Blocked: {he.blocked}")
            logger.info(f"    Exit Code: {he.exit_code}")
            if he.additional_context:
                # Truncate for readability
                ctx = (
                    he.additional_context[:500] + "..."
                    if len(he.additional_context) > 500
                    else he.additional_context
                )
                logger.info(f"    Additional Context: {ctx}")
            if he.error:
                logger.info(f"    Error: {he.error}")

        # Count stop hooks that were denied (pre-commit failed)
        stop_events = [e for e in hook_events if e.hook_event_type == "Stop"]
        denied_stops = [e for e in stop_events if e.blocked]

        logger.info(f"\nStop hook events: {len(stop_events)}")
        logger.info(f"Denied stops (pre-commit failures): {len(denied_stops)}")

        if denied_stops:
            logger.info(
                "\n✅ SUCCESS: Stop hook denied at least once due to "
                "pre-commit failure!"
            )
            logger.info(
                "   The agent should have received feedback and fixed the issue."
            )
        else:
            logger.info(
                "\n⚠️  No denied stops detected. Either pre-commit passed on first "
                "try or the hook didn't work as expected."
            )

        # Demonstrate state.events functionality
        logger.info("\n" + "=" * 50)
        logger.info("📊 Demonstrating State Events API")
        logger.info("=" * 50)

        # Count total events using state.events
        total_events = len(conversation.state.events)
        logger.info(f"📈 Total events in conversation: {total_events}")

        # Get recent events (last 10) using state.events
        logger.info("\n🔍 Getting last 10 events using state.events...")
        all_events = conversation.state.events
        recent_events = all_events[-10:] if len(all_events) >= 10 else all_events

        for i, event in enumerate(recent_events, 1):
            event_type = type(event).__name__
            timestamp = getattr(event, "timestamp", "Unknown")
            logger.info(f"  {i}. {event_type} at {timestamp}")

        # Let's see what the actual event types are
        logger.info("\n🔍 Event types found in recent events:")
        event_types = set()
        for event in recent_events:
            event_type = type(event).__name__
            event_types.add(event_type)
        for event_type in sorted(event_types):
            logger.info(f"  - {event_type}")

        # Print all ConversationStateUpdateEvent
        logger.info("\n🗂️  ConversationStateUpdateEvent events:")
        for event in conversation.state.events:
            if isinstance(event, ConversationStateUpdateEvent):
                logger.info(f"  - {event}")

        cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
        print(f"EXAMPLE_COST: {cost}")

    finally:
        # Clean up
        print("\n🧹 Cleaning up conversation...")
        conversation.close()
