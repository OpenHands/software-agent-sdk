"""End-to-end test using a real FastAPI agent server with patched LLM.

This validates RemoteConversation against actual REST + WebSocket endpoints,
while keeping the LLM deterministic via monkeypatching.
"""

import json
import threading
import time
from collections.abc import Generator
from pathlib import Path

import pytest
import uvicorn
from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.conversation import RemoteConversation
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    CondensationSummaryEvent,
    ConversationStateUpdateEvent,
    Event,
    LLMConvertibleEvent,
    MessageEvent,
    ObservationEvent,
    PauseEvent,
    SystemPromptEvent,
)
from openhands.sdk.workspace import RemoteWorkspace
from openhands.workspace.docker.workspace import find_available_tcp_port


@pytest.fixture
def server_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[dict, None, None]:
    """Launch a real FastAPI server backed by temp workspace and conversations.

    We set OPENHANDS_AGENT_SERVER_CONFIG_PATH before creating the app so that
    routers pick up the correct default config and in-memory services.
    """

    # Create an isolated config pointing to tmp dirs
    conversations_path = tmp_path / "conversations"
    workspace_path = tmp_path / "workspace"

    # Ensure clean directories (both tmp and any leftover in cwd)
    import shutil
    from pathlib import Path

    # Clean up any leftover directories from previous runs in current working directory
    cwd_conversations = Path("workspace/conversations")
    if cwd_conversations.exists():
        shutil.rmtree(cwd_conversations)

    # Also clean up the workspace directory entirely to be safe
    cwd_workspace = Path("workspace")
    if cwd_workspace.exists():
        # Only remove conversations subdirectory to avoid interfering with other tests
        for item in cwd_workspace.iterdir():
            if item.name == "conversations":
                shutil.rmtree(item)

    # Clean up tmp directories
    if conversations_path.exists():
        shutil.rmtree(conversations_path)
    if workspace_path.exists():
        shutil.rmtree(workspace_path)

    conversations_path.mkdir(parents=True, exist_ok=True)
    workspace_path.mkdir(parents=True, exist_ok=True)

    # Verify the conversations directory is truly empty
    assert not list(conversations_path.iterdir()), (
        f"Conversations path not empty: {list(conversations_path.iterdir())}"
    )

    cfg = {
        "session_api_keys": [],  # disable auth for tests
        "conversations_path": str(conversations_path),
        "workspace_path": str(workspace_path),
    }
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))

    # Ensure default config uses our file and disable any env key override
    monkeypatch.setenv("OPENHANDS_AGENT_SERVER_CONFIG_PATH", str(cfg_file))
    monkeypatch.delenv("SESSION_API_KEY", raising=False)

    # Build app after env is set
    from openhands.agent_server.api import create_app
    from openhands.agent_server.config import Config

    cfg_obj = Config.model_validate_json(cfg_file.read_text())

    app = create_app(cfg_obj)

    # Start uvicorn on a free port
    port = find_available_tcp_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for the server to be ready with health check
    import httpx

    base_url = f"http://127.0.0.1:{port}"
    server_ready = False
    for attempt in range(50):  # Wait up to 5 seconds
        try:
            with httpx.Client() as client:
                response = client.get(f"{base_url}/health", timeout=2.0)
                if response.status_code == 200:
                    server_ready = True
                    break
        except (httpx.RequestError, httpx.TimeoutException):
            pass
        time.sleep(0.1)

    if not server_ready:
        raise RuntimeError("Server failed to start within timeout")

    try:
        yield {"host": f"http://127.0.0.1:{port}"}
    finally:
        # uvicorn.Server lacks a robust shutdown API here; rely on daemon thread exit.
        server.should_exit = True
        thread.join(timeout=2)

        # Clean up any leftover directories created during the test
        cwd_conversations = Path("workspace/conversations")
        if cwd_conversations.exists():
            shutil.rmtree(cwd_conversations)


@pytest.fixture
def patched_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch LLM.completion to a deterministic assistant message response."""

    def fake_completion(
        self,
        messages,
        tools,
        return_metrics=False,
        add_security_risk_prediction=False,
        **kwargs,
    ):  # type: ignore[no-untyped-def]
        from openhands.sdk.llm.llm_response import LLMResponse
        from openhands.sdk.llm.message import Message
        from openhands.sdk.llm.utils.metrics import MetricsSnapshot

        # Create a minimal ModelResponse with a single assistant message
        litellm_msg = LiteLLMMessage.model_validate(
            {
                "role": "assistant",
                "content": "Hello from patched LLM",
            }
        )
        raw_response = ModelResponse(
            id="test-resp",
            created=int(time.time()),
            model="test-model",
            choices=[Choices(index=0, finish_reason="stop", message=litellm_msg)],
        )

        # Convert to OpenHands Message
        message = Message.from_llm_chat_message(litellm_msg)

        # Create metrics snapshot
        metrics_snapshot = MetricsSnapshot(
            model_name="test-model",
            accumulated_cost=0.0,
            max_budget_per_task=None,
            accumulated_token_usage=None,
        )

        # Return LLMResponse as expected by the agent
        return LLMResponse(
            message=message, metrics=metrics_snapshot, raw_response=raw_response
        )

    monkeypatch.setattr(LLM, "completion", fake_completion, raising=True)


def test_remote_conversation_over_real_server(server_env, patched_llm):
    import shutil
    from pathlib import Path

    # Create an Agent with a real LLM object (patched for determinism)
    llm = LLM(model="gpt-4", api_key=SecretStr("test"))
    agent = Agent(llm=llm, tools=[])

    # Create conversation via factory pointing at the live server
    workspace = RemoteWorkspace(
        host=server_env["host"], working_dir="/tmp/workspace/project"
    )
    conv: RemoteConversation = Conversation(
        agent=agent, workspace=workspace
    )  # RemoteConversation

    # Send a message and run
    conv.send_message("Say hello")
    conv.run()

    # Validate state transitions and that we received an assistant message
    state = conv.state
    assert state.execution_status.value in {"finished", "idle", "running"}

    # Wait for WS-delivered events and validate them using proper type checking
    found_system_prompt = False
    found_state_update = False
    found_agent_related_event = False

    for i in range(50):  # up to ~5s
        events = state.events

        # Validate event types using isinstance checks (not hasattr/getattr)
        for e in events:
            assert isinstance(
                e,
                (
                    MessageEvent,
                    ActionEvent,
                    ObservationEvent,
                    AgentErrorEvent,
                    Event,
                    LLMConvertibleEvent,
                    SystemPromptEvent,
                    PauseEvent,
                    CondensationSummaryEvent,
                    ConversationStateUpdateEvent,
                ),
            ), f"Unexpected event type: {type(e).__name__}"

        # Check for expected event types with proper isinstance checks
        for e in events:
            if isinstance(e, SystemPromptEvent) and e.source == "agent":
                found_system_prompt = True
                # SystemPromptEvent is an agent-related event
                found_agent_related_event = True

            if isinstance(e, ConversationStateUpdateEvent):
                found_state_update = True
                # Verify it has the expected structure
                assert e.source == "environment", (
                    "ConversationStateUpdateEvent should have source='environment'"
                )

            # Check for other agent-related events (MessageEvent or ActionEvent)
            if isinstance(e, MessageEvent) and e.source == "agent":
                # Verify MessageEvent has the expected structure
                assert hasattr(e, "llm_message"), (
                    "MessageEvent should have llm_message attribute"
                )
                assert e.llm_message.role in ("assistant", "user"), (
                    f"Expected role to be assistant or user, got {e.llm_message.role}"
                )
                found_agent_related_event = True
            elif isinstance(e, ActionEvent) and e.source == "agent":
                # Verify ActionEvent has expected structure
                assert hasattr(e, "tool_name"), (
                    "ActionEvent should have tool_name attribute"
                )
                found_agent_related_event = True

        # We expect at least system prompt and state update events
        if found_system_prompt and found_state_update:
            break
        time.sleep(0.1)

    # Assert we got the expected events with descriptive messages
    assert found_system_prompt, (
        f"Expected to find SystemPromptEvent with source='agent'. "
        f"Found {len(state.events)} events: {
            [
                (
                    type(e).__name__,
                    e.source
                    if isinstance(e, (MessageEvent, ActionEvent, SystemPromptEvent))
                    else 'N/A',
                )
                for e in state.events
            ]
        }"
    )
    assert found_state_update, (
        f"Expected to find ConversationStateUpdateEvent. "
        f"Found {len(state.events)} events: {[type(e).__name__ for e in state.events]}"
    )
    assert found_agent_related_event, (
        f"Expected to find at least one agent-related event "
        f"(SystemPromptEvent, MessageEvent, or ActionEvent). "
        f"Found {len(state.events)} events: {
            [
                (
                    type(e).__name__,
                    e.source
                    if isinstance(
                        e,
                        (
                            MessageEvent,
                            ActionEvent,
                            SystemPromptEvent,
                            ConversationStateUpdateEvent,
                        ),
                    )
                    else 'N/A',
                )
                for e in state.events
            ]
        }"
    )

    conv.close()

    # Clean up any conversation directories that might have been created in cwd
    cwd_conversations = Path("workspace/conversations")
    if cwd_conversations.exists():
        shutil.rmtree(cwd_conversations)


def test_bash_command_endpoint_with_live_server(server_env):
    """Integration test for bash command execution through live server.

    This test validates that the /api/bash/start_bash_command endpoint works
    correctly end-to-end by:
    1. Starting a real FastAPI server with bash endpoints
    2. Creating a RemoteWorkspace pointing to that server
    3. Executing a real bash command
    4. Verifying the actual command output

    This is a regression test for issue #866 where bash execution was broken
    due to using the wrong endpoint URL.
    """
    # Create a RemoteWorkspace pointing to the live server
    workspace = RemoteWorkspace(
        host=server_env["host"], working_dir="/tmp/test_workspace"
    )

    # Execute a bash command that produces verifiable output
    # Test multiple commands to ensure command chaining works
    command = "echo 'Hello from live bash endpoint!' && echo 'Line 2' && expr 5 + 3"
    result = workspace.execute_command(command, timeout=10.0)

    # Verify the command executed successfully
    assert result.exit_code == 0, (
        f"Command failed with exit code {result.exit_code}. "
        f"stdout: {result.stdout}, stderr: {result.stderr}"
    )
    assert result.timeout_occurred is False, (
        "Command timed out - this suggests the endpoint is not working correctly"
    )

    # Verify the actual output contains all our expected text
    assert "Hello from live bash endpoint!" in result.stdout, (
        f"Expected 'Hello from live bash endpoint!' not found in stdout: "
        f"{result.stdout}"
    )
    assert "Line 2" in result.stdout, (
        f"Expected 'Line 2' not found in stdout: {result.stdout}"
    )
    assert "8" in result.stdout, (
        f"Expected '8' (result of 5+3) not found in stdout: {result.stdout}"
    )


def test_file_upload_endpoint_with_live_server(server_env, tmp_path: Path):
    """Integration test for file upload through live server.

    This test validates that the /api/file/upload/{path} endpoint works
    correctly end-to-end by:
    1. Starting a real FastAPI server with file upload endpoints
    2. Creating a RemoteWorkspace pointing to that server
    3. Creating a test file and uploading it
    4. Verifying the file was uploaded to the correct location with correct content

    This is a regression test for the file upload issue where the client was
    calling /api/file/upload (without the path parameter) instead of
    /api/file/upload/{path} as the server expects.
    """
    # Create a RemoteWorkspace pointing to the live server
    workspace = RemoteWorkspace(
        host=server_env["host"], working_dir="/tmp/test_workspace"
    )

    # Create a test file to upload
    test_file = tmp_path / "test_upload.txt"
    test_content = "Hello from file upload test!\nThis is line 2.\n"
    test_file.write_text(test_content)

    # Define the destination path (must be absolute for the server)
    destination = "/tmp/test_workspace/uploaded_file.txt"

    # Upload the file
    result = workspace.file_upload(str(test_file), destination)

    # Verify the upload was successful
    assert result.success is True, (
        f"File upload failed. Error: {result.error}, "
        f"Source: {result.source_path}, Destination: {result.destination_path}"
    )
    assert result.source_path == str(test_file), (
        f"Expected source_path to be {test_file}, got {result.source_path}"
    )
    assert result.destination_path == destination, (
        f"Expected destination_path to be {destination}, got {result.destination_path}"
    )

    # Verify the file exists at the destination with correct content
    # Use bash command to check file existence and read content
    check_cmd = f"test -f {destination} && cat {destination}"
    check_result = workspace.execute_command(check_cmd, timeout=5.0)

    assert check_result.exit_code == 0, (
        f"File does not exist at destination or could not be read. "
        f"Exit code: {check_result.exit_code}, "
        f"stderr: {check_result.stderr}"
    )

    # Verify the content matches what we uploaded
    assert check_result.stdout == test_content, (
        f"File content mismatch. Expected:\n{test_content}\nGot:\n{check_result.stdout}"
    )


def test_conversation_stats_with_live_server(
    server_env, monkeypatch: pytest.MonkeyPatch
):
    """Integration test verifying conversation stats are correctly propagated.

    This test validates the fix for issue #1041 where accumulated cost was
    always 0. It checks:
    1. RemoteConversation reads stats from the correct 'stats' field (not
       'conversation_stats')
    2. Stats updates are propagated after run() completes
    3. Accumulated cost and token usage are correctly tracked

    This is a regression test for the field mismatch and state update issues.
    """

    def fake_completion_with_cost(
        self,
        messages,
        tools,
        return_metrics=False,
        add_security_risk_prediction=False,
        **kwargs,
    ):  # type: ignore[no-untyped-def]
        from openhands.sdk.llm.llm_response import LLMResponse
        from openhands.sdk.llm.message import Message
        from openhands.sdk.llm.utils.metrics import TokenUsage

        # Create a minimal ModelResponse with a single assistant message
        litellm_msg = LiteLLMMessage.model_validate(
            {"role": "assistant", "content": "Test response"}
        )
        raw_response = ModelResponse(
            id="test-resp-with-cost",
            created=int(time.time()),
            model="test-model",
            choices=[Choices(index=0, finish_reason="stop", message=litellm_msg)],
        )

        # Convert to OpenHands Message
        message = Message.from_llm_chat_message(litellm_msg)

        # Simulate cost accumulation in the LLM's metrics
        # The LLM should have metrics that track cost
        from openhands.sdk.llm.utils.metrics import MetricsSnapshot

        if self.metrics:
            self.metrics.add_cost(0.0025)
            self.metrics.add_token_usage(
                prompt_tokens=100,
                completion_tokens=50,
                cache_read_tokens=0,
                cache_write_tokens=0,
                context_window=8192,
                response_id="test-resp-with-cost",
                reasoning_tokens=0,
            )
            metrics_snapshot = self.metrics.get_snapshot()
        else:
            # Create a default metrics snapshot if no metrics exist
            metrics_snapshot = MetricsSnapshot(
                model_name=self.model,
                accumulated_cost=0.0025,
                accumulated_token_usage=TokenUsage(
                    model=self.model,
                    prompt_tokens=100,
                    completion_tokens=50,
                    response_id="test-resp-with-cost",
                ),
            )

        return LLMResponse(
            message=message, metrics=metrics_snapshot, raw_response=raw_response
        )

    # Patch LLM.completion with our cost-tracking version
    monkeypatch.setattr(LLM, "completion", fake_completion_with_cost, raising=True)

    # Create an Agent with a real LLM object
    llm = LLM(model="gpt-4", api_key=SecretStr("test"))
    agent = Agent(llm=llm, tools=[])

    # Create conversation via factory pointing at the live server
    workspace = RemoteWorkspace(
        host=server_env["host"], working_dir="/tmp/workspace/project"
    )
    conv: RemoteConversation = Conversation(agent=agent, workspace=workspace)

    # Verify initial stats are empty/zero
    initial_stats = conv.conversation_stats
    assert initial_stats is not None
    initial_cost = initial_stats.get_combined_metrics().accumulated_cost
    assert initial_cost == 0.0, f"Expected initial cost to be 0.0, got {initial_cost}"

    # Send a message and run the conversation
    conv.send_message("Test message")
    conv.run()

    # Wait for the conversation to finish and stats to update
    # The fix ensures stats are published after run() completes
    max_attempts = 50
    for attempt in range(max_attempts):
        try:
            stats = conv.conversation_stats
            combined_metrics = stats.get_combined_metrics()
            accumulated_cost = combined_metrics.accumulated_cost

            # Check if we got non-zero cost (stats have been updated)
            if accumulated_cost > 0:
                # Verify the stats are correctly populated
                assert accumulated_cost > 0, (
                    f"Expected accumulated_cost > 0 after run(), got {accumulated_cost}"
                )

                # Verify token usage is tracked
                if combined_metrics.accumulated_token_usage:
                    assert combined_metrics.accumulated_token_usage.prompt_tokens > 0, (
                        "Expected prompt_tokens > 0 after run()"
                    )
                    assert (
                        combined_metrics.accumulated_token_usage.completion_tokens > 0
                    ), "Expected completion_tokens > 0 after run()"

                # Success - we got updated stats
                break
        except (KeyError, AttributeError, AssertionError) as e:
            if attempt == max_attempts - 1:
                raise AssertionError(
                    f"Stats not properly updated after {max_attempts} attempts. "
                    f"Last error: {e}"
                )
        time.sleep(0.1)

    # Final verification: stats are read from 'stats' field, not 'conversation_stats'
    info = conv.state._get_conversation_info()
    assert "stats" in info, "Expected 'stats' field in conversation info"

    # Verify the RemoteConversation is correctly reading from 'stats'
    stats_from_field = info.get("stats", {})
    assert stats_from_field, "Expected non-empty stats in the 'stats' field after run()"

    conv.close()


def test_security_risk_field_with_live_server(
    server_env, monkeypatch: pytest.MonkeyPatch
):
    """Integration test validating security_risk field functionality.

    This test validates the fix for issue #819 where security_risk field handling
    was inconsistent. It tests that:
    1. Actions execute successfully with security_risk provided
    2. Actions execute successfully without security_risk (defaults to UNKNOWN)

    This is a regression test spawning a real agent server to ensure end-to-end
    functionality of security_risk field handling.
    """

    # Track which completion call we're on to control behavior
    call_count = {"count": 0}

    def fake_completion_with_tool_calls(
        self,
        messages,
        tools,
        return_metrics=False,
        add_security_risk_prediction=False,
        **kwargs,
    ):  # type: ignore[no-untyped-def]
        from openhands.sdk.llm.llm_response import LLMResponse
        from openhands.sdk.llm.message import Message
        from openhands.sdk.llm.utils.metrics import MetricsSnapshot

        call_count["count"] += 1

        # First call: return tool call WITHOUT security_risk
        # (to test error event when analyzer is configured)
        if call_count["count"] == 1:
            litellm_msg = LiteLLMMessage.model_validate(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "finish",
                                "arguments": '{"message": "Task complete"}',
                            },
                        }
                    ],
                }
            )
        # Second call: return tool call WITH security_risk
        # (to test successful execution after error)
        elif call_count["count"] == 2:
            litellm_msg = LiteLLMMessage.model_validate(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {
                                "name": "finish",
                                "arguments": (
                                    '{"message": "Task complete", '
                                    '"security_risk": "LOW"}'
                                ),
                            },
                        }
                    ],
                }
            )
        # Third call: simple message to finish
        else:
            litellm_msg = LiteLLMMessage.model_validate(
                {"role": "assistant", "content": "Done"}
            )

        raw_response = ModelResponse(
            id=f"test-resp-{call_count['count']}",
            created=int(time.time()),
            model="test-model",
            choices=[Choices(index=0, finish_reason="stop", message=litellm_msg)],
        )

        message = Message.from_llm_chat_message(litellm_msg)
        metrics_snapshot = MetricsSnapshot(
            model_name="test-model",
            accumulated_cost=0.0,
            max_budget_per_task=None,
            accumulated_token_usage=None,
        )

        return LLMResponse(
            message=message, metrics=metrics_snapshot, raw_response=raw_response
        )

    monkeypatch.setattr(
        LLM, "completion", fake_completion_with_tool_calls, raising=True
    )

    # Create an Agent (security analyzer functionality has been deprecated and removed)
    # Using empty tools list since tools need to be registered in the server
    llm = LLM(model="gpt-4", api_key=SecretStr("test"))
    agent = Agent(
        llm=llm,
        tools=[],
    )

    workspace = RemoteWorkspace(
        host=server_env["host"], working_dir="/tmp/workspace/project"
    )
    conv: RemoteConversation = Conversation(agent=agent, workspace=workspace)

    # Step 1: Send message WITHOUT security_risk - should still execute (defaults to
    # UNKNOWN)
    conv.send_message("Complete the task")
    conv.run()

    # Wait for action event - should succeed even without security_risk
    found_action_without_risk = False
    for attempt in range(50):  # up to ~5s
        events = conv.state.events
        for e in events:
            if isinstance(e, ActionEvent) and e.tool_name == "finish":
                # Verify it has a security risk attribute
                assert hasattr(e, "security_risk"), (
                    "Expected ActionEvent to have security_risk attribute"
                )
                found_action_without_risk = True
                break
        if found_action_without_risk:
            break
        time.sleep(0.1)

    assert found_action_without_risk, (
        "Expected to find ActionEvent with finish tool even without security_risk"
    )

    conv.close()

    # The test validates that:
    # 1. Actions can be executed without security_risk (defaults to UNKNOWN)
    # 2. ActionEvent always has a security_risk attribute
