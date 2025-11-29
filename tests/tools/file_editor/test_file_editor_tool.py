"""Tests for FileEditorTool subclass."""

import os
import tempfile
from uuid import uuid4

from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.file_editor import (
    FileEditorAction,
    FileEditorObservation,
    FileEditorTool,
)


def _create_test_conv_state(temp_dir: str) -> ConversationState:
    """Helper to create a test conversation state."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    return ConversationState.create(
        id=uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=temp_dir),
    )


def test_file_editor_tool_initialization():
    """Test that FileEditorTool initializes correctly."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = FileEditorTool.create(conv_state)
        tool = tools[0]

        # Check that the tool has the correct name and properties
        assert tool.name == "file_editor"
        assert tool.executor is not None
        assert issubclass(tool.action_type, FileEditorAction)


def test_file_editor_tool_create_file():
    """Test that FileEditorTool can create files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = FileEditorTool.create(conv_state)
        tool = tools[0]

        test_file = os.path.join(temp_dir, "test.txt")

        # Create an action to create a file
        action = FileEditorAction(
            command="create",
            path=test_file,
            file_text="Hello, World!",
        )

        # Execute the action
        result = tool(action)

        # Check the result
        assert result is not None
        assert isinstance(result, FileEditorObservation)
        assert not result.is_error
        assert os.path.exists(test_file)

        # Check file contents
        with open(test_file) as f:
            content = f.read()
        assert content == "Hello, World!"


def test_file_editor_tool_view_file():
    """Test that FileEditorTool can view files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = FileEditorTool.create(conv_state)
        tool = tools[0]

        test_file = os.path.join(temp_dir, "test.txt")

        # Create a test file
        with open(test_file, "w") as f:
            f.write("Line 1\nLine 2\nLine 3")

        # Create an action to view the file
        action = FileEditorAction(command="view", path=test_file)

        # Execute the action
        result = tool(action)

        # Check the result
        assert result is not None
        assert isinstance(result, FileEditorObservation)
        assert not result.is_error
        assert "Line 1" in result.text
        assert "Line 2" in result.text
        assert "Line 3" in result.text


def test_file_editor_tool_str_replace():
    """Test that FileEditorTool can perform string replacement."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = FileEditorTool.create(conv_state)
        tool = tools[0]

        test_file = os.path.join(temp_dir, "test.txt")

        # Create a test file
        with open(test_file, "w") as f:
            f.write("Hello, World!\nThis is a test.")

        # Create an action to replace text
        action = FileEditorAction(
            command="str_replace",
            path=test_file,
            old_str="World",
            new_str="Universe",
        )

        # Execute the action
        result = tool(action)

        # Check the result
        assert result is not None
        assert isinstance(result, FileEditorObservation)
        assert not result.is_error

        # Check file contents
        with open(test_file) as f:
            content = f.read()
        assert "Hello, Universe!" in content


def test_file_editor_tool_to_openai_tool():
    """Test that FileEditorTool can be converted to OpenAI tool format."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = FileEditorTool.create(conv_state)
        tool = tools[0]

        # Convert to OpenAI tool format
        openai_tool = tool.to_openai_tool()

        # Check the format
        assert openai_tool["type"] == "function"
        assert openai_tool["function"]["name"] == "file_editor"
        assert "description" in openai_tool["function"]
        assert "parameters" in openai_tool["function"]


def test_file_editor_tool_view_directory():
    """Test that FileEditorTool can view directories."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = FileEditorTool.create(conv_state)
        tool = tools[0]

        # Create some test files
        test_file1 = os.path.join(temp_dir, "file1.txt")
        test_file2 = os.path.join(temp_dir, "file2.txt")

        with open(test_file1, "w") as f:
            f.write("File 1 content")
        with open(test_file2, "w") as f:
            f.write("File 2 content")

        # Create an action to view the directory
        action = FileEditorAction(command="view", path=temp_dir)

        # Execute the action
        result = tool(action)

        # Check the result
        assert result is not None
        assert isinstance(result, FileEditorObservation)
        assert not result.is_error
        assert "file1.txt" in result.text
        assert "file2.txt" in result.text


def test_file_editor_tool_includes_working_directory_in_description():
    """Test that FileEditorTool includes working directory info in description."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = FileEditorTool.create(conv_state)
        tool = tools[0]

        # Check that the tool description includes working directory information
        assert f"Your current working directory is: {temp_dir}" in tool.description
        assert (
            "When exploring project structure, start with this directory "
            "instead of the root filesystem."
        ) in tool.description

        # Verify the original description is still there
        assert (
            "Custom editing tool for viewing, creating and editing files"
            in tool.description
        )


def test_file_editor_tool_openai_format_includes_working_directory():
    """Test that OpenAI tool format includes working directory info."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = FileEditorTool.create(conv_state)
        tool = tools[0]

        # Convert to OpenAI tool format
        openai_tool = tool.to_openai_tool()

        # Check that the description includes working directory information
        function_def = openai_tool["function"]
        assert "description" in function_def
        description = function_def["description"]
        assert f"Your current working directory is: {temp_dir}" in description
        assert (
            "When exploring project structure, start with this directory "
            "instead of the root filesystem."
        ) in description


def test_file_editor_tool_image_viewing_line_with_vision_enabled():
    """Test that image viewing line is included when LLM supports vision."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create LLM with vision support (gpt-4o-mini supports vision)
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])
        conv_state = ConversationState.create(
            id=uuid4(),
            agent=agent,
            workspace=LocalWorkspace(working_dir=temp_dir),
        )

        tools = FileEditorTool.create(conv_state)
        tool = tools[0]

        # Check that the image viewing line is included in description
        assert (
            "If `path` is an image file (.png, .jpg, .jpeg, .gif, .webp, .bmp)"
            in tool.description
        )
        assert "view` displays the image content" in tool.description


def test_file_editor_tool_image_viewing_line_with_vision_disabled():
    """Test that image viewing line is excluded when LLM doesn't support vision."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create LLM without vision support (gpt-3.5-turbo doesn't support vision)
        llm = LLM(
            model="gpt-3.5-turbo", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        agent = Agent(llm=llm, tools=[])
        conv_state = ConversationState.create(
            id=uuid4(),
            agent=agent,
            workspace=LocalWorkspace(working_dir=temp_dir),
        )

        tools = FileEditorTool.create(conv_state)
        tool = tools[0]

        # Check that the image viewing line is NOT included in description
        assert "is an image file" not in tool.description
        assert "displays the image content" not in tool.description
