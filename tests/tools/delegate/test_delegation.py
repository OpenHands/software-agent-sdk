"""Tests for delegation tools."""

import json
import uuid
from unittest.mock import MagicMock, patch

from pydantic import SecretStr

from openhands.sdk.agent.utils import fix_malformed_tool_arguments
from openhands.sdk.conversation.conversation_stats import ConversationStats
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


def test_spawn_gives_sub_agents_independent_metrics():
    """Sub-agents must not share the parent's Metrics object."""
    register_builtins_agents()
    parent_llm = LLM(
        model="openai/gpt-4o",
        api_key=SecretStr("test-key"),
        base_url="https://api.openai.com/v1",
    )

    parent_conversation = MagicMock()
    parent_conversation.id = uuid.uuid4()
    parent_conversation.agent.llm = parent_llm
    parent_conversation.state.workspace.working_dir = "/tmp"
    parent_conversation._visualizer = None

    executor = DelegateExecutor()
    spawn_action = DelegateAction(command="spawn", ids=["a1", "a2"])
    executor(spawn_action, parent_conversation)

    a1_llm = executor._sub_agents["a1"].agent.llm
    a2_llm = executor._sub_agents["a2"].agent.llm

    # Each sub-agent must have its own Metrics, not the parent's
    assert a1_llm.metrics is not parent_llm.metrics
    assert a2_llm.metrics is not parent_llm.metrics
    assert a1_llm.metrics is not a2_llm.metrics

    # Mutating a sub-agent's metrics must not affect the parent
    before = parent_llm.metrics.accumulated_cost
    a1_llm.metrics.add_cost(1.00)
    assert parent_llm.metrics.accumulated_cost == before
    a2_llm.metrics.add_cost(1.00)
    assert parent_llm.metrics.accumulated_cost == before


def test_delegate_merges_metrics_into_parent():
    """After delegation, sub-agent metrics appear in parent stats."""
    register_builtins_agents()
    parent_llm = LLM(
        model="openai/gpt-4o",
        api_key=SecretStr("test-key"),
        base_url="https://api.openai.com/v1",
    )
    parent_stats = ConversationStats()
    parent_stats.usage_to_metrics["agent"] = parent_llm.metrics

    parent_conversation = MagicMock()
    parent_conversation.id = uuid.uuid4()
    parent_conversation.agent.llm = parent_llm
    parent_conversation.state.workspace.working_dir = "/tmp"
    parent_conversation._visualizer = None
    parent_conversation.conversation_stats = parent_stats

    executor = DelegateExecutor()
    spawn_action = DelegateAction(command="spawn", ids=["a1", "a2"])
    executor(spawn_action, parent_conversation)

    # Wire LLMs into sub-conv stats (simulates what _ensure_agent_ready does)
    for agent_id in ("a1", "a2"):
        sub_conv = executor._sub_agents[agent_id]
        llm = sub_conv.agent.llm
        sub_conv.conversation_stats.usage_to_metrics[llm.usage_id] = llm.metrics

    # Simulate sub-agent LLM usage
    a1_llm = executor._sub_agents["a1"].agent.llm
    a2_llm = executor._sub_agents["a2"].agent.llm
    a1_llm.metrics.add_cost(1.00)
    a1_llm.metrics.add_token_usage(
        prompt_tokens=100,
        completion_tokens=50,
        cache_read_tokens=0,
        cache_write_tokens=0,
        context_window=128000,
        response_id="a1_r1",
    )
    a2_llm.metrics.add_cost(2.00)
    a2_llm.metrics.add_token_usage(
        prompt_tokens=200,
        completion_tokens=100,
        cache_read_tokens=0,
        cache_write_tokens=0,
        context_window=128000,
        response_id="a2_r1",
    )

    # Run delegation (patching send_message/run so no real LLM calls happen)
    with (
        patch.object(executor._sub_agents["a1"], "send_message"),
        patch.object(executor._sub_agents["a1"], "run"),
        patch.object(executor._sub_agents["a2"], "send_message"),
        patch.object(executor._sub_agents["a2"], "run"),
    ):
        delegate_action = DelegateAction(
            command="delegate",
            tasks={"a1": "task 1", "a2": "task 2"},
        )
        executor(delegate_action, parent_conversation)

    # Sub-agent metrics are now in parent stats under delegate: keys
    assert "delegate:a1" in parent_stats.usage_to_metrics
    assert "delegate:a2" in parent_stats.usage_to_metrics
    assert parent_stats.usage_to_metrics["delegate:a1"].accumulated_cost == 1.00
    assert parent_stats.usage_to_metrics["delegate:a2"].accumulated_cost == 2.00

    # Combined total includes parent + both sub-agents
    combined = parent_stats.get_combined_metrics()
    assert combined.accumulated_cost == 3.00
    accumulated_token_usage = combined.accumulated_token_usage
    assert accumulated_token_usage is not None
    assert accumulated_token_usage.prompt_tokens == 300
    assert accumulated_token_usage.completion_tokens == 150


def test_repeated_delegation_does_not_double_count():
    """Delegating to the same agent twice must not duplicate metrics."""
    register_builtins_agents()
    parent_llm = LLM(
        model="openai/gpt-4o",
        api_key=SecretStr("test-key"),
        base_url="https://api.openai.com/v1",
    )
    parent_stats = ConversationStats()
    parent_stats.usage_to_metrics["agent"] = parent_llm.metrics

    parent_conversation = MagicMock()
    parent_conversation.id = uuid.uuid4()
    parent_conversation.agent.llm = parent_llm
    parent_conversation.state.workspace.working_dir = "/tmp"
    parent_conversation._visualizer = None
    parent_conversation.conversation_stats = parent_stats

    executor = DelegateExecutor()
    spawn_action = DelegateAction(command="spawn", ids=["a1"])
    executor(spawn_action, parent_conversation)

    sub_conv = executor._sub_agents["a1"]
    sub_conv.conversation_stats.usage_to_metrics[sub_conv.agent.llm.usage_id] = (
        sub_conv.agent.llm.metrics
    )

    a1_llm = executor._sub_agents["a1"].agent.llm

    # First delegation: sub-agent accumulates $1.00
    a1_llm.metrics.add_cost(1.00)
    with (
        patch.object(executor._sub_agents["a1"], "send_message"),
        patch.object(executor._sub_agents["a1"], "run"),
    ):
        executor(
            DelegateAction(command="delegate", tasks={"a1": "first task"}),
            parent_conversation,
        )
    assert parent_stats.usage_to_metrics["delegate:a1"].accumulated_cost == 1.00

    # Second delegation: sub-agent accumulates another $2.00 (cumulative $3.00)
    a1_llm.metrics.add_cost(2.00)
    with (
        patch.object(executor._sub_agents["a1"], "send_message"),
        patch.object(executor._sub_agents["a1"], "run"),
    ):
        executor(
            DelegateAction(command="delegate", tasks={"a1": "second task"}),
            parent_conversation,
        )

    # Must be $3.00 (cumulative), not $4.00 (double-counted)
    assert parent_stats.usage_to_metrics["delegate:a1"].accumulated_cost == 3.00


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
