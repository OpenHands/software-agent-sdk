"""Tests for delegation tools."""

import uuid
from unittest.mock import MagicMock, patch

from pydantic import SecretStr

from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm import LLM
from openhands.tools.delegate import (
    DelegateAction,
    DelegateExecutor,
    DelegateObservation,
)


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
    # Test spawn observation
    spawn_observation = DelegateObservation(
        command="spawn",
        message="Sub-agents created successfully",
    )
    assert spawn_observation.command == "spawn"
    assert spawn_observation.message == "Sub-agents created successfully"
    # spawn observation doesn't have results field anymore

    # Test delegate observation
    delegate_observation = DelegateObservation(
        command="delegate",
        message="Tasks completed successfully\n\nResults:\n1. Result 1\n2. Result 2",
    )
    assert delegate_observation.command == "delegate"
    assert "Tasks completed successfully" in delegate_observation.message
    assert "Result 1" in delegate_observation.message
    assert "Result 2" in delegate_observation.message


def test_delegate_executor_delegate():
    """Test DelegateExecutor delegate operation."""
    executor, parent_conversation = create_test_executor_and_parent()

    # First spawn some agents
    spawn_action = DelegateAction(command="spawn", ids=["agent1", "agent2"])
    spawn_observation = executor(spawn_action, parent_conversation)
    assert "Successfully spawned" in spawn_observation.message

    # Then delegate tasks to them
    delegate_action = DelegateAction(
        command="delegate",
        tasks={"agent1": "Analyze code quality", "agent2": "Write tests"},
    )

    with patch.object(executor, "_delegate_tasks") as mock_delegate:
        mock_observation = DelegateObservation(
            command="delegate",
            message=(
                "Tasks completed successfully\n\nResults:\n"
                "1. Agent agent1: Code analysis complete\n"
                "2. Agent agent2: Tests written"
            ),
        )
        mock_delegate.return_value = mock_observation

        observation = executor(delegate_action, parent_conversation)

    assert isinstance(observation, DelegateObservation)
    assert observation.command == "delegate"
    assert "Agent agent1: Code analysis complete" in observation.message
    assert "Agent agent2: Tests written" in observation.message


def test_delegate_executor_missing_task():
    """Test DelegateExecutor delegate with empty tasks dict."""
    executor, parent_conversation = create_test_executor_and_parent()

    # Test delegate action with no tasks
    action = DelegateAction(command="delegate", tasks={})

    observation = executor(action, parent_conversation)

    assert isinstance(observation, DelegateObservation)
    assert observation.command == "delegate"
    assert (
        "task is required" in observation.message.lower()
        or "at least one task" in observation.message.lower()
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
