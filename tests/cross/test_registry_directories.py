"""Test directory handling in tool registry."""

import os
import tempfile
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
from openhands.sdk.event.llm_convertible import SystemPromptEvent
from openhands.sdk.llm import LLM, TextContent
from openhands.sdk.tool.registry import resolve_tool
from openhands.sdk.tool.spec import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool


class DummyAgent(AgentBase):
    """Test agent for directory testing."""

    def __init__(self):
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        super().__init__(llm=llm, tools=[])

    def init_state(
        self, state: ConversationState, on_event: ConversationCallbackType
    ) -> None:
        event = SystemPromptEvent(
            source="agent", system_prompt=TextContent(text="test agent"), tools=[]
        )
        on_event(event)

    def step(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ) -> None:
        pass


@pytest.fixture
def test_agent():
    """Create a test agent for testing."""
    return DummyAgent()


@pytest.fixture(autouse=True)
def register_tools():
    """Register tools for testing."""
    from openhands.sdk.tool import register_tool

    register_tool("TerminalTool", TerminalTool)
    register_tool("FileEditorTool", FileEditorTool)
    register_tool("TaskTrackerTool", TaskTrackerTool)


def test_resolve_tool_with_conversation_directories(test_agent):
    """Test that resolve_tool uses directories from conversation."""
    with tempfile.TemporaryDirectory() as temp_dir:
        working_dir = os.path.join(temp_dir, "work")
        persistence_dir = os.path.join(temp_dir, "persist")
        os.makedirs(working_dir)
        os.makedirs(persistence_dir)

        conversation = Conversation(
            agent=test_agent,
            persistence_dir=persistence_dir,
            workspace=working_dir,
        )

        # Test TerminalTool
        bash_tool = Tool(name="TerminalTool")
        bash_tools = resolve_tool(bash_tool, conv_state=conversation._state)
        assert len(bash_tools) == 1
        # Type ignore needed for test-specific executor access
        work_dir = bash_tools[0].executor.session.work_dir  # type: ignore[attr-defined]
        assert work_dir == working_dir

        # Test FileEditorTool
        editor_tool = Tool(name="FileEditorTool")
        editor_tools = resolve_tool(editor_tool, conv_state=conversation._state)
        assert len(editor_tools) == 1
        # Type ignore needed for test-specific executor access
        cwd = str(editor_tools[0].executor.editor._cwd)  # type: ignore[attr-defined]
        assert cwd == working_dir

        # Test TaskTrackerTool
        tracker_tool = Tool(name="TaskTrackerTool")
        tracker_tools = resolve_tool(tracker_tool, conv_state=conversation._state)
        assert len(tracker_tools) == 1
        # Type ignore needed for test-specific executor access
        save_dir = str(tracker_tools[0].executor.save_dir)  # type: ignore[attr-defined]
        # TaskTrackerTool uses conversation's persistence_dir which includes
        # conversation ID
        expected_save_dir = str(Path(persistence_dir) / conversation._state.id.hex)
        assert save_dir == expected_save_dir
