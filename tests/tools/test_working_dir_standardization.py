"""Test that tools use standardized working directory.

This test verifies that issue #211 has been resolved:
"Standardize input argument for openhands tools"

Both TerminalTool (BashTool) and FileEditorTool (StrReplaceEditorTool) should use
the same source for working directory: conv_state.workspace.working_dir
"""

import tempfile
from uuid import uuid4

from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.file_editor import FileEditorAction, FileEditorTool
from openhands.tools.terminal import TerminalAction, TerminalTool


def _create_test_conv_state(temp_dir: str) -> ConversationState:
    """Helper to create a test conversation state."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    return ConversationState.create(
        id=uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=temp_dir),
    )


def test_terminal_and_file_editor_use_same_working_dir():
    """Test that TerminalTool and FileEditorTool use the same working directory.

    This is a regression test for issue #211 to ensure that both tools
    get their working directory from conv_state.workspace.working_dir.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)

        # Create both tools from the same conv_state
        terminal_tools = TerminalTool.create(conv_state)
        file_editor_tools = FileEditorTool.create(conv_state)

        terminal_tool = terminal_tools[0]
        file_editor_tool = file_editor_tools[0]

        # Verify terminal tool uses the correct working directory
        pwd_action = TerminalAction(command="pwd")
        pwd_result = terminal_tool(pwd_action)
        assert temp_dir in pwd_result.text, (
            f"TerminalTool should use working_dir from conv_state.workspace. "
            f"Expected {temp_dir} in output, got: {pwd_result.text}"
        )

        # Verify file editor tool uses the correct working directory
        # by checking that the description includes the working directory
        assert temp_dir in file_editor_tool.description, (
            f"FileEditorTool should include working_dir in description. "
            f"Expected {temp_dir} in description."
        )

        # Verify file editor can create files in the working directory
        test_file = f"{temp_dir}/test_standardization.txt"
        create_action = FileEditorAction(
            command="create",
            path=test_file,
            file_text="Test content",
        )
        create_result = file_editor_tool(create_action)
        assert not create_result.is_error, (
            f"FileEditorTool should be able to create files in working_dir. "
            f"Error: {create_result.text}"
        )


def test_tools_do_not_require_params_for_working_dir():
    """Test that tools don't require params={'working_dir': ...} anymore.

    This verifies that the old pattern of passing working_dir via params
    has been removed and tools now get it from conv_state.workspace.working_dir.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)

        # Both tools should be creatable without any params for working_dir
        # The create() method only takes conv_state (and optional tool-specific params)
        terminal_tools = TerminalTool.create(conv_state)
        file_editor_tools = FileEditorTool.create(conv_state)

        # Verify tools were created successfully
        assert len(terminal_tools) == 1
        assert len(file_editor_tools) == 1

        # Verify tools have executors
        assert terminal_tools[0].executor is not None
        assert file_editor_tools[0].executor is not None
