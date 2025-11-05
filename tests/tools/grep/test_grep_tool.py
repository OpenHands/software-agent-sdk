"""Tests for GrepTool integration."""

import os
import tempfile
from pathlib import Path
from uuid import uuid4

from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.grep import GrepAction, GrepObservation, GrepTool


def _create_test_conv_state(temp_dir: str) -> ConversationState:
    """Helper to create a test conversation state."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    return ConversationState.create(
        id=uuid4(),
        workspace=LocalWorkspace(working_dir=temp_dir),
        agent=agent,
    )


def test_grep_tool_initialization():
    """Test that GrepTool initializes correctly."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = GrepTool.create(conv_state)

        assert len(tools) == 1
        tool = tools[0]
        assert tool.name == "grep"
        assert tool.executor is not None


def test_grep_tool_invalid_working_dir():
    """Test that GrepTool raises error for invalid working directory."""
    try:
        conv_state = _create_test_conv_state("/nonexistent/directory")
        GrepTool.create(conv_state)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "not a valid directory" in str(e)


def test_grep_tool_basic_search():
    """Test basic grep search returns file paths."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create test files
        (Path(temp_dir) / "app.py").write_text("print('hello')")
        (Path(temp_dir) / "utils.py").write_text("print('world')")

        conv_state = _create_test_conv_state(temp_dir)
        tools = GrepTool.create(conv_state)
        tool = tools[0]

        action = GrepAction(pattern="print")
        assert tool.executor is not None
        observation = tool.executor(action)

        assert isinstance(observation, GrepObservation)
        assert observation.is_error is False
        assert len(observation.matches) == 2  # Two files
        assert observation.pattern == "print"
        assert observation.search_path == str(Path(temp_dir).resolve())
        assert not observation.truncated

        # Check that matches are file paths
        for file_path in observation.matches:
            assert isinstance(file_path, str)
            assert file_path.endswith(".py")
            assert os.path.exists(file_path)


def test_grep_tool_case_insensitive():
    """Test that grep is case-insensitive."""
    with tempfile.TemporaryDirectory() as temp_dir:
        (Path(temp_dir) / "test.py").write_text("PRINT('test')")

        conv_state = _create_test_conv_state(temp_dir)
        tools = GrepTool.create(conv_state)
        tool = tools[0]

        action = GrepAction(pattern="print")
        assert tool.executor is not None
        observation = tool.executor(action)

        assert observation.is_error is False
        assert len(observation.matches) == 1


def test_grep_tool_include_filter():
    """Test include filter for file patterns."""
    with tempfile.TemporaryDirectory() as temp_dir:
        (Path(temp_dir) / "test.py").write_text("test")
        (Path(temp_dir) / "test.js").write_text("test")

        conv_state = _create_test_conv_state(temp_dir)
        tools = GrepTool.create(conv_state)
        tool = tools[0]

        action = GrepAction(pattern="test", include="*.py")
        assert tool.executor is not None
        observation = tool.executor(action)

        assert observation.is_error is False
        assert len(observation.matches) == 1
        assert observation.matches[0].endswith(".py")


def test_grep_tool_specific_directory():
    """Test searching in specific directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        src_dir = Path(temp_dir) / "src"
        src_dir.mkdir()
        (src_dir / "source.py").write_text("print('source')")
        (Path(temp_dir) / "other.py").write_text("print('other')")

        conv_state = _create_test_conv_state(temp_dir)
        tools = GrepTool.create(conv_state)
        tool = tools[0]

        action = GrepAction(pattern="print", path=str(src_dir))
        assert tool.executor is not None
        observation = tool.executor(action)

        assert observation.is_error is False
        assert len(observation.matches) == 1
        assert observation.search_path == str(src_dir.resolve())
        assert str(src_dir) in observation.matches[0]


def test_grep_tool_no_matches():
    """Test when no files contain the pattern."""
    with tempfile.TemporaryDirectory() as temp_dir:
        (Path(temp_dir) / "app.py").write_text("def main():\n    return 0")

        conv_state = _create_test_conv_state(temp_dir)
        tools = GrepTool.create(conv_state)
        tool = tools[0]

        action = GrepAction(pattern="nonexistent")
        assert tool.executor is not None
        observation = tool.executor(action)

        assert observation.is_error is False
        assert len(observation.matches) == 0
        assert not observation.truncated


def test_grep_tool_invalid_regex():
    """Test handling of invalid regex."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = GrepTool.create(conv_state)
        tool = tools[0]

        action = GrepAction(pattern="[invalid")
        assert tool.executor is not None
        observation = tool.executor(action)

        assert observation.is_error is True
        assert "Invalid regex pattern" in observation.text


def test_grep_tool_invalid_directory():
    """Test searching in invalid directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = GrepTool.create(conv_state)
        tool = tools[0]

        action = GrepAction(pattern="test", path="/nonexistent/path")
        assert tool.executor is not None
        observation = tool.executor(action)

        assert observation.is_error is True
        assert "not a valid directory" in observation.text


def test_grep_tool_hidden_files_excluded():
    """Test that hidden files are excluded from results."""
    with tempfile.TemporaryDirectory() as temp_dir:
        (Path(temp_dir) / "visible.py").write_text("test")
        (Path(temp_dir) / ".hidden.py").write_text("test")

        conv_state = _create_test_conv_state(temp_dir)
        tools = GrepTool.create(conv_state)
        tool = tools[0]

        action = GrepAction(pattern="test")
        assert tool.executor is not None
        observation = tool.executor(action)

        assert observation.is_error is False
        assert len(observation.matches) == 1
        assert ".hidden" not in observation.matches[0]


def test_grep_tool_to_llm_content():
    """Test conversion of observation to LLM content."""
    with tempfile.TemporaryDirectory() as temp_dir:
        (Path(temp_dir) / "test.py").write_text("test content")

        conv_state = _create_test_conv_state(temp_dir)
        tools = GrepTool.create(conv_state)
        tool = tools[0]

        action = GrepAction(pattern="test")
        assert tool.executor is not None
        observation = tool.executor(action)

        content = observation.to_llm_content
        assert len(content) == 1
        text = content[0].text
        assert "Found 1 file(s) containing pattern" in text
        assert "test.py" in text


def test_grep_tool_to_llm_content_with_include():
    """Test LLM content includes filter info."""
    with tempfile.TemporaryDirectory() as temp_dir:
        (Path(temp_dir) / "test.py").write_text("test")

        conv_state = _create_test_conv_state(temp_dir)
        tools = GrepTool.create(conv_state)
        tool = tools[0]

        action = GrepAction(pattern="test", include="*.py")
        assert tool.executor is not None
        observation = tool.executor(action)

        content = observation.to_llm_content
        text = content[0].text
        assert "(filtered by '*.py')" in text


def test_grep_tool_to_llm_content_no_matches():
    """Test LLM content for no matches."""
    with tempfile.TemporaryDirectory() as temp_dir:
        (Path(temp_dir) / "test.py").write_text("content")

        conv_state = _create_test_conv_state(temp_dir)
        tools = GrepTool.create(conv_state)
        tool = tools[0]

        action = GrepAction(pattern="nonexistent")
        assert tool.executor is not None
        observation = tool.executor(action)

        content = observation.to_llm_content
        text = content[0].text
        assert "No files found containing pattern" in text


def test_grep_tool_to_llm_content_error():
    """Test LLM content for errors."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = GrepTool.create(conv_state)
        tool = tools[0]

        action = GrepAction(pattern="[invalid")
        assert tool.executor is not None
        observation = tool.executor(action)

        content = observation.to_llm_content
        assert len(content) == 2
        assert content[0].text == GrepObservation.ERROR_MESSAGE_HEADER
        text = content[1].text
        assert "Invalid regex pattern" in text


def test_grep_tool_truncation():
    """Test that truncation is indicated in results."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create 150 files
        for i in range(150):
            (Path(temp_dir) / f"file{i}.py").write_text("test")

        conv_state = _create_test_conv_state(temp_dir)
        tools = GrepTool.create(conv_state)
        tool = tools[0]

        action = GrepAction(pattern="test")
        assert tool.executor is not None
        observation = tool.executor(action)

        assert observation.is_error is False
        assert len(observation.matches) == 100
        assert observation.truncated is True

        content = observation.to_llm_content
        text = content[0].text
        assert "truncated" in text.lower()
