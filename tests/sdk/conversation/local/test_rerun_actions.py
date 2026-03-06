"""Tests for conversation.rerun_actions() functionality."""

from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.conversation.types import (
    ConversationCallbackType,
    ConversationTokenCallbackType,
)
from openhands.sdk.event import ActionEvent
from openhands.sdk.event.llm_convertible import MessageEvent, SystemPromptEvent
from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent
from openhands.sdk.tool import (
    Action,
    Observation,
    Tool,
    ToolDefinition,
    ToolExecutor,
    register_tool as register_tool_public,
    registry as tool_registry,
)


def _make_action_event(
    tool_name: str,
    action: Action,
    tool_call_id: str = "tc1",
) -> ActionEvent:
    """Helper to create ActionEvent with all required fields."""
    return ActionEvent(
        source="agent",
        thought=[TextContent(text="test thought")],
        action=action,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_call=MessageToolCall(
            id=tool_call_id,
            name=tool_name,
            arguments="{}",
            origin="completion",
        ),
        llm_response_id="response_1",
    )


# Track execution counts for testing
execution_counts: dict[str, int] = {}


class RerunTestAction(Action):
    """Test action for rerun tests."""

    value: str = "test"


class RerunTestObservation(Observation):
    """Test observation for rerun tests."""

    result: str = ""
    execution_count: int = 0


class RerunTestExecutor(ToolExecutor[RerunTestAction, RerunTestObservation]):
    """Test executor that tracks execution counts."""

    def __call__(
        self,
        action: RerunTestAction,
        conversation: "LocalConversation | None" = None,
    ) -> RerunTestObservation:
        # Track how many times each action value was executed
        key = action.value
        execution_counts[key] = execution_counts.get(key, 0) + 1
        return RerunTestObservation.from_text(
            f"executed: {action.value} (count: {execution_counts[key]})",
            result=f"result_{action.value}",
            execution_count=execution_counts[key],
        )


class RerunTestTool(ToolDefinition[RerunTestAction, RerunTestObservation]):
    """Test tool for rerun tests."""

    @classmethod
    def create(cls, conv_state=None, **params):
        return [
            cls(
                description="A test tool for testing rerun_actions",
                action_type=RerunTestAction,
                observation_type=RerunTestObservation,
                executor=RerunTestExecutor(),
            )
        ]


@pytest.fixture(autouse=True)
def _reset_execution_counts():
    """Reset execution counts before each test."""
    execution_counts.clear()
    yield
    execution_counts.clear()


@pytest.fixture(autouse=True)
def _tool_registry_isolation(monkeypatch: pytest.MonkeyPatch):
    """Isolate tool registry per test using monkeypatch.

    This ensures test tools are registered without affecting the global registry
    and automatically cleans up after each test.
    """
    # Create isolated copies of the registry dictionaries
    isolated_reg = dict(tool_registry._REG)
    isolated_qualnames = dict(tool_registry._MODULE_QUALNAMES)

    # Patch the registry to use isolated copies
    monkeypatch.setattr(tool_registry, "_REG", isolated_reg)
    monkeypatch.setattr(tool_registry, "_MODULE_QUALNAMES", isolated_qualnames)

    # Register our test tool in the isolated registry
    register_tool_public(RerunTestTool.name, RerunTestTool)


class RerunDummyAgent(AgentBase):
    """Dummy agent for testing rerun_actions."""

    def __init__(self, tools=None):
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        super().__init__(llm=llm, tools=tools or [])

    def init_state(
        self, state: ConversationState, on_event: ConversationCallbackType
    ) -> None:
        super().init_state(state, on_event)
        event = SystemPromptEvent(
            source="agent", system_prompt=TextContent(text="dummy"), tools=[]
        )
        on_event(event)

    def step(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ) -> None:
        on_event(
            MessageEvent(
                source="agent",
                llm_message=Message(role="assistant", content=[TextContent(text="ok")]),
            )
        )


def test_rerun_actions_empty_conversation():
    """Test rerun_actions on a conversation with no actions."""
    agent = RerunDummyAgent(tools=[Tool(name="rerun_test", params={})])
    conversation = Conversation(agent=agent)

    # Rerun on empty conversation should return empty list
    observations = conversation.rerun_actions()
    assert observations == []


def test_rerun_actions_basic():
    """Test basic rerun_actions functionality."""
    agent = RerunDummyAgent(tools=[Tool(name="rerun_test", params={})])
    conversation = Conversation(agent=agent)

    # Execute some tools to create action events
    action1 = RerunTestAction(value="first")
    action2 = RerunTestAction(value="second")

    # Manually add action events to simulate a conversation history
    conversation._ensure_agent_ready()
    action_event = _make_action_event("rerun_test", action1, "tc1")
    conversation._state.events.append(action_event)

    action_event2 = _make_action_event("rerun_test", action2, "tc2")
    conversation._state.events.append(action_event2)

    # Now rerun all actions
    observations = conversation.rerun_actions()

    # Should have executed both actions
    assert len(observations) == 2
    # Cast to our specific observation type to access result
    obs1 = observations[0]
    obs2 = observations[1]
    assert isinstance(obs1, RerunTestObservation)
    assert isinstance(obs2, RerunTestObservation)
    assert obs1.result == "result_first"
    assert obs2.result == "result_second"
    assert execution_counts["first"] == 1
    assert execution_counts["second"] == 1


def test_rerun_actions_preserves_original_observations():
    """Test that rerun_actions doesn't modify the original event log."""
    agent = RerunDummyAgent(tools=[Tool(name="rerun_test", params={})])
    conversation = Conversation(agent=agent)

    # Add an action event
    conversation._ensure_agent_ready()
    action = RerunTestAction(value="preserve_test")
    action_event = _make_action_event("rerun_test", action, "tc1")
    conversation._state.events.append(action_event)

    # Count events before rerun
    events_before = len(list(conversation._state.events))

    # Rerun actions
    observations = conversation.rerun_actions()

    # Count events after rerun - should be the same
    events_after = len(list(conversation._state.events))

    assert events_before == events_after
    assert len(observations) == 1


def test_rerun_actions_skips_none_actions():
    """Test that rerun_actions skips ActionEvents with action=None."""
    agent = RerunDummyAgent(tools=[Tool(name="rerun_test", params={})])
    conversation = Conversation(agent=agent)

    conversation._ensure_agent_ready()

    # Add an action event with action=None (failed validation)
    action_event_none = ActionEvent(
        source="agent",
        thought=[TextContent(text="test")],
        tool_name="rerun_test",
        tool_call_id="tc1",
        tool_call=MessageToolCall(
            id="tc1", name="rerun_test", arguments="{}", origin="completion"
        ),
        llm_response_id="resp1",
        action=None,  # Failed validation
    )
    conversation._state.events.append(action_event_none)

    # Add a valid action event
    action = RerunTestAction(value="valid")
    action_event_valid = _make_action_event("rerun_test", action, "tc2")
    conversation._state.events.append(action_event_valid)

    # Rerun should only execute the valid action
    observations = conversation.rerun_actions()

    assert len(observations) == 1
    obs = observations[0]
    assert isinstance(obs, RerunTestObservation)
    assert obs.result == "result_valid"


def test_rerun_actions_missing_tool_raises():
    """Test that rerun_actions raises KeyError for missing tools."""
    agent = RerunDummyAgent(tools=[])  # No tools registered
    conversation = Conversation(agent=agent)

    conversation._ensure_agent_ready()

    # Add an action event for a tool that doesn't exist
    action = RerunTestAction(value="test")
    action_event = _make_action_event("rerun_test", action, "tc1")
    conversation._state.events.append(action_event)

    with pytest.raises(KeyError) as exc_info:
        conversation.rerun_actions()

    assert "rerun_test" in str(exc_info.value)
    assert "not found during rerun" in str(exc_info.value)


def test_rerun_can_be_called_manually():
    """Test that rerun_actions can be called manually after initialization."""
    agent = RerunDummyAgent(tools=[Tool(name="rerun_test", params={})])
    conversation = Conversation(agent=agent)

    conversation._ensure_agent_ready()
    action = RerunTestAction(value="manual")
    action_event = _make_action_event("rerun_test", action, "tc1")
    conversation._state.events.append(action_event)

    # Call rerun manually (not during init)
    observations = conversation.rerun_actions()

    assert len(observations) == 1
    assert execution_counts["manual"] == 1

    # Can call again
    observations2 = conversation.rerun_actions()

    assert len(observations2) == 1
    assert execution_counts["manual"] == 2  # Executed twice now


# =============================================================================
# Tests with Real File Operations
# =============================================================================
# These tests verify that rerun_actions actually reproduces environment state
# using real file system operations.


class FileWriteAction(Action):
    """Action that writes content to a file."""

    filepath: str
    content: str


class FileWriteObservation(Observation):
    """Observation returned from file write operations."""

    filepath: str = ""
    written: bool = False


class FileWriteExecutor(ToolExecutor[FileWriteAction, FileWriteObservation]):
    """Executor that writes content to a real file."""

    def __call__(
        self,
        action: FileWriteAction,
        conversation: "LocalConversation | None" = None,
    ) -> FileWriteObservation:
        path = Path(action.filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(action.content)
        return FileWriteObservation.from_text(
            f"Written to {action.filepath}",
            filepath=action.filepath,
            written=True,
        )


class FileWriteTool(ToolDefinition[FileWriteAction, FileWriteObservation]):
    """Tool that writes content to files."""

    @classmethod
    def create(cls, conv_state=None, **params):
        return [
            cls(
                description="Write content to a file",
                action_type=FileWriteAction,
                observation_type=FileWriteObservation,
                executor=FileWriteExecutor(),
            )
        ]


class FileCreateAction(Action):
    """Action that creates a new file (fails if file exists)."""

    filepath: str
    content: str


class FileCreateObservation(Observation):
    """Observation returned from file create operations."""

    filepath: str = ""
    created: bool = False


class FileCreateExecutor(ToolExecutor[FileCreateAction, FileCreateObservation]):
    """Executor that creates a new file (fails if exists)."""

    def __call__(
        self,
        action: FileCreateAction,
        conversation: "LocalConversation | None" = None,
    ) -> FileCreateObservation:
        path = Path(action.filepath)
        if path.exists():
            return FileCreateObservation.from_text(
                f"Error: File {action.filepath} already exists",
                filepath=action.filepath,
                created=False,
                is_error=True,
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(action.content)
        return FileCreateObservation.from_text(
            f"Created {action.filepath}",
            filepath=action.filepath,
            created=True,
        )


class FileCreateTool(ToolDefinition[FileCreateAction, FileCreateObservation]):
    """Tool that creates new files (non-idempotent)."""

    @classmethod
    def create(cls, conv_state=None, **params):
        return [
            cls(
                description="Create a new file (fails if exists)",
                action_type=FileCreateAction,
                observation_type=FileCreateObservation,
                executor=FileCreateExecutor(),
            )
        ]


class FailingAction(Action):
    """Action that always fails."""

    message: str = "fail"


class FailingObservation(Observation):
    """Observation from failing tool."""

    pass


class FailingExecutor(ToolExecutor[FailingAction, FailingObservation]):
    """Executor that always raises an exception."""

    def __call__(
        self,
        action: FailingAction,
        conversation: "LocalConversation | None" = None,
    ) -> FailingObservation:
        raise RuntimeError(f"Intentional failure: {action.message}")


class FailingTool(ToolDefinition[FailingAction, FailingObservation]):
    """Tool that always fails."""

    @classmethod
    def create(cls, conv_state=None, **params):
        return [
            cls(
                description="A tool that always fails",
                action_type=FailingAction,
                observation_type=FailingObservation,
                executor=FailingExecutor(),
            )
        ]


def test_rerun_reproduces_file_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test that rerun_actions reproduces file system state.

    This test verifies the main use case: create a file, clear workspace,
    rerun actions, and verify the file is recreated.
    """
    # Register the file write tool
    register_tool_public(FileWriteTool.name, FileWriteTool)

    agent = RerunDummyAgent(tools=[Tool(name="file_write", params={})])
    conversation = Conversation(agent=agent)
    conversation._ensure_agent_ready()

    # Create action that writes a file
    test_file = tmp_path / "test_file.txt"
    action = FileWriteAction(filepath=str(test_file), content="hello world")
    action_event = _make_action_event("file_write", action, "tc1")
    conversation._state.events.append(action_event)

    # First rerun creates the file
    observations = conversation.rerun_actions()
    assert len(observations) == 1
    assert test_file.exists()
    assert test_file.read_text() == "hello world"

    # Clear the file
    test_file.unlink()
    assert not test_file.exists()

    # Rerun again - file should be recreated
    observations2 = conversation.rerun_actions()
    assert len(observations2) == 1
    assert test_file.exists()
    assert test_file.read_text() == "hello world"


def test_rerun_non_idempotent_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test that non-idempotent operations fail on rerun when state exists.

    This verifies the documented non-idempotency warning: file creation
    will fail if the file already exists.
    """
    # Register the file create tool (non-idempotent)
    register_tool_public(FileCreateTool.name, FileCreateTool)

    agent = RerunDummyAgent(tools=[Tool(name="file_create", params={})])
    conversation = Conversation(agent=agent)
    conversation._ensure_agent_ready()

    test_file = tmp_path / "new_file.txt"
    action = FileCreateAction(filepath=str(test_file), content="content")
    action_event = _make_action_event("file_create", action, "tc1")
    conversation._state.events.append(action_event)

    # First rerun creates the file successfully
    observations = conversation.rerun_actions()
    assert len(observations) == 1
    obs = observations[0]
    assert isinstance(obs, FileCreateObservation)
    assert obs.created is True
    assert test_file.exists()

    # Second rerun - file already exists, should return error observation
    observations2 = conversation.rerun_actions()
    assert len(observations2) == 1
    obs2 = observations2[0]
    assert isinstance(obs2, FileCreateObservation)
    assert obs2.created is False
    assert obs2.is_error is True
    assert "already exists" in obs2.text


def test_rerun_partial_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test rerun with a mix of successful and failing actions.

    This verifies that rerun continues processing even when some actions fail,
    and returns observations only for successful executions.
    """
    # Register both tools
    register_tool_public(FileWriteTool.name, FileWriteTool)
    register_tool_public(FailingTool.name, FailingTool)

    agent = RerunDummyAgent(
        tools=[
            Tool(name="file_write", params={}),
            Tool(name="failing", params={}),
        ]
    )
    conversation = Conversation(agent=agent)
    conversation._ensure_agent_ready()

    # Add a successful action
    test_file1 = tmp_path / "file1.txt"
    action1 = FileWriteAction(filepath=str(test_file1), content="first")
    conversation._state.events.append(_make_action_event("file_write", action1, "tc1"))

    # Add a failing action
    action2 = FailingAction(message="intentional")
    conversation._state.events.append(_make_action_event("failing", action2, "tc2"))

    # Add another successful action
    test_file2 = tmp_path / "file2.txt"
    action3 = FileWriteAction(filepath=str(test_file2), content="second")
    conversation._state.events.append(_make_action_event("file_write", action3, "tc3"))

    # Rerun - should process all actions, returning observations for successes
    observations = conversation.rerun_actions()

    # Should have 2 observations (the failing one doesn't add to the list)
    assert len(observations) == 2

    # Both files should be created despite the middle failure
    assert test_file1.exists()
    assert test_file1.read_text() == "first"
    assert test_file2.exists()
    assert test_file2.read_text() == "second"


def test_rerun_multiple_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test rerun with multiple file operations in sequence."""
    register_tool_public(FileWriteTool.name, FileWriteTool)

    agent = RerunDummyAgent(tools=[Tool(name="file_write", params={})])
    conversation = Conversation(agent=agent)
    conversation._ensure_agent_ready()

    # Create multiple file write actions
    files_content = [
        ("file_a.txt", "content A"),
        ("file_b.txt", "content B"),
        ("subdir/file_c.txt", "content C"),
    ]

    for i, (filename, content) in enumerate(files_content):
        action = FileWriteAction(
            filepath=str(tmp_path / filename),
            content=content,
        )
        conversation._state.events.append(
            _make_action_event("file_write", action, f"tc{i}")
        )

    # Rerun all actions
    observations = conversation.rerun_actions()

    # All files should be created
    assert len(observations) == 3
    for filename, expected_content in files_content:
        file_path = tmp_path / filename
        assert file_path.exists(), f"File {filename} should exist"
        assert file_path.read_text() == expected_content
