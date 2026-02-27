"""Tests for delegation tools."""

import json
import uuid
from unittest.mock import MagicMock, patch

from pydantic import SecretStr

from openhands.sdk.agent.utils import fix_malformed_tool_arguments
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm import LLM, TextContent
from openhands.sdk.subagent.registry import register_builtins_agents
from openhands.tools.delegate import (
    DelegateExecutor,
    DelegateObservation,
)
from openhands.tools.delegate.definition import DelegateAction


def create_test_executor_and_parent():
    """Helper to create test executor and parent conversation."""
    llm = LLM(
        model="openai/gpt-4o",
        api_key=SecretStr("test-key"),
        base_url="https://api.openai.com/v1",
    )

    parent_conversation = MagicMock()
    parent_conversation.id = uuid.uuid4()
    parent_conversation.agent.llm = llm
    parent_conversation.agent.cli_mode = True
    parent_conversation.state.workspace.working_dir = "/tmp"
    parent_conversation.visualize = False

    executor = DelegateExecutor()

    return executor, parent_conversation


def create_mock_conversation():
    """Helper to create a mock conversation."""
    mock_conv = MagicMock()
    mock_conv.id = str(uuid.uuid4())
    mock_conv.state.execution_status = ConversationExecutionStatus.FINISHED
    return mock_conv


def test_delegate_action_creation():
    """Test creating DelegateAction instances."""
    # Test spawn action
    spawn_action = DelegateAction(command="spawn", ids=["agent1", "agent2"])
    assert spawn_action.command == "spawn"
    assert spawn_action.ids == ["agent1", "agent2"]
    assert spawn_action.tasks is None

    # Test delegate action
    delegate_action = DelegateAction(
        command="delegate",
        tasks={"agent1": "Analyze code quality", "agent2": "Write tests"},
    )
    assert delegate_action.command == "delegate"
    assert delegate_action.tasks == {
        "agent1": "Analyze code quality",
        "agent2": "Write tests",
    }
    assert delegate_action.ids is None


def test_delegate_observation_creation():
    """Test creating DelegateObservation instances."""
    # Test spawn observation with string output
    spawn_observation = DelegateObservation.from_text(
        text="spawn: Sub-agents created successfully",
        command="spawn",
    )
    assert isinstance(spawn_observation.content, list)
    assert spawn_observation.text == "spawn: Sub-agents created successfully"
    # Verify to_llm_content returns TextContent
    llm_content = spawn_observation.to_llm_content
    assert len(llm_content) == 1
    assert isinstance(llm_content[0], TextContent)
    assert llm_content[0].text == "spawn: Sub-agents created successfully"

    # Test delegate observation with string output
    delegate_observation = DelegateObservation.from_text(
        text=(
            "delegate: Tasks completed successfully\n\nResults:\n"
            "1. Result 1\n2. Result 2"
        ),
        command="delegate",
    )
    assert isinstance(delegate_observation.content, list)
    assert "Tasks completed successfully" in delegate_observation.text
    assert "Result 1" in delegate_observation.text
    assert "Result 2" in delegate_observation.text
    # Verify to_llm_content
    llm_content = delegate_observation.to_llm_content
    assert len(llm_content) == 1
    assert isinstance(llm_content[0], TextContent)
    assert "Tasks completed successfully" in llm_content[0].text


def test_delegate_executor_delegate():
    """Test DelegateExecutor delegate operation."""
    executor, parent_conversation = create_test_executor_and_parent()
    register_builtins_agents()
    # First spawn some agents
    spawn_action = DelegateAction(command="spawn", ids=["agent1", "agent2"])
    spawn_observation = executor(spawn_action, parent_conversation)
    assert isinstance(spawn_observation.content, list)
    assert "Successfully spawned" in spawn_observation.text

    # Then delegate tasks to them
    delegate_action = DelegateAction(
        command="delegate",
        tasks={"agent1": "Analyze code quality", "agent2": "Write tests"},
    )

    with patch.object(executor, "_delegate_tasks") as mock_delegate:
        mock_observation = DelegateObservation.from_text(
            text=(
                "delegate: Tasks completed successfully\n\nResults:\n"
                "1. Agent agent1: Code analysis complete\n"
                "2. Agent agent2: Tests written"
            ),
            command="delegate",
        )
        mock_delegate.return_value = mock_observation

        observation = executor(delegate_action, parent_conversation)

    assert isinstance(observation, DelegateObservation)
    assert isinstance(observation.content, list)
    text_content = observation.text
    assert "Agent agent1: Code analysis complete" in text_content
    assert "Agent agent2: Tests written" in text_content


def test_delegate_executor_missing_task():
    """Test DelegateExecutor delegate with empty tasks dict."""
    executor, parent_conversation = create_test_executor_and_parent()

    # Test delegate action with no tasks
    action = DelegateAction(command="delegate", tasks={})

    observation = executor(action, parent_conversation)

    assert isinstance(observation, DelegateObservation)
    # Error message should be in the error field
    assert observation.is_error
    assert observation.is_error is True
    content_text = observation.text
    assert (
        "task is required" in content_text.lower()
        or "at least one task" in content_text.lower()
    )


def test_delegation_manager_init():
    """Test DelegateExecutor initialization."""
    mock_conv = create_mock_conversation()
    manager = DelegateExecutor()

    manager._parent_conversation = mock_conv

    # Test that we can access the parent conversation
    assert manager.parent_conversation == mock_conv
    assert str(manager.parent_conversation.id) == str(mock_conv.id)

    # Test that sub-agents dict is empty initially
    assert len(manager._sub_agents) == 0


def test_spawn_disables_streaming_for_sub_agents():
    """Test that spawned sub-agents have streaming disabled.

    This prevents the 'Streaming requires an on_token callback' error
    when the parent conversation has streaming enabled but sub-agents
    don't have token callbacks.
    """
    # Create parent LLM with streaming enabled
    parent_llm = LLM(
        model="openai/gpt-4o",
        api_key=SecretStr("test-key"),
        base_url="https://api.openai.com/v1",
        stream=True,  # Parent has streaming enabled
    )
    register_builtins_agents()

    parent_conversation = MagicMock()
    parent_conversation.id = uuid.uuid4()
    parent_conversation.agent.llm = parent_llm
    parent_conversation.agent.cli_mode = True
    parent_conversation.state.workspace.working_dir = "/tmp"
    parent_conversation._visualizer = None

    executor = DelegateExecutor()

    # Spawn an agent
    spawn_action = DelegateAction(command="spawn", ids=["test_agent"])
    observation = executor(spawn_action, parent_conversation)

    # Verify spawn succeeded
    assert "Successfully spawned" in observation.text
    assert "test_agent" in executor._sub_agents

    # Verify the sub-agent's LLM has streaming disabled
    sub_conversation = executor._sub_agents["test_agent"]
    sub_llm = sub_conversation.agent.llm
    assert sub_llm.stream is False, "Sub-agent LLM should have streaming disabled"

    # Verify parent LLM still has streaming enabled (wasn't mutated)
    assert parent_llm.stream is True, "Parent LLM should still have streaming enabled"


def test_issue_2216():
    """Reproduce issue #2216: DelegateAction rejects tasks sent as a JSON string.

    When an LLM serialises the `tasks` dict as a JSON *string* (instead of a
    JSON object), the values inside that string may contain newlines.  After the
    outer `json.loads` of the tool-call arguments the `\\n` escapes become
    real newline characters, which makes the inner string invalid JSON.
    `fix_malformed_tool_arguments` silently fails to parse it and passes the
    raw string to `DelegateAction.model_validate`, which then raises a
    `ValidationError`.

    Ref: https://github.com/OpenHands/software-agent-sdk/issues/2216
    """
    # Raw JSON exactly as the LLM emits it — tasks is a *string*, not an object,
    # and the task description contains a ``\n`` (valid JSON escape for newline).
    raw_llm_args = (
        '{"command": "delegate",'
        ' "tasks": "{\\"batch1\\": \\"Build TWO apps\\nFollow instructions\\"}"}'
    )

    # Outer parse succeeds — tasks is now a Python str with a real newline.
    arguments = json.loads(raw_llm_args)
    assert isinstance(arguments["tasks"], str)
    assert "\n" in arguments["tasks"]

    # fix_malformed_tool_arguments should convert it to a dict
    # so that model_validate accepts it.
    fixed = fix_malformed_tool_arguments(arguments, DelegateAction)
    action = DelegateAction.model_validate(fixed)
    assert isinstance(action.tasks, dict)
    assert action.tasks == {"batch1": "Build TWO apps\nFollow instructions"}
