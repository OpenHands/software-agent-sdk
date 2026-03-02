"""Tests for GlobTool subclass."""

import os
import tempfile
from pathlib import Path
from uuid import uuid4

from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.glob import GlobAction, GlobObservation, GlobTool


def _create_test_conv_state(temp_dir: str) -> ConversationState:
    """Helper to create a test conversation state."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    return ConversationState.create(
        id=uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=temp_dir),
    )


def test_glob_tool_initialization():
    """Test that GlobTool initializes correctly."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = GlobTool.create(conv_state)
        tool = tools[0]

        # Check that the tool has the correct name and properties
        assert tool.name == "glob"
        assert tool.executor is not None
        assert tool.action_type == GlobAction
        assert tool.observation_type == GlobObservation


def test_glob_tool_invalid_working_dir():
    """Test that GlobTool raises error for invalid working directory."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    conv_state = ConversationState.create(
        id=uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir="/nonexistent/directory"),
    )

    try:
        GlobTool.create(conv_state)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "is not a valid directory" in str(e)


def test_glob_tool_find_files():
    """Test that GlobTool can find files with patterns."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create test files
        test_files = [
            "test.py",
            "main.js",
            "config.json",
            "src/app.py",
            "src/utils.js",
            "tests/test_main.py",
        ]

        for file_path in test_files:
            full_path = Path(temp_dir) / file_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(f"# Content of {file_path}")

        conv_state = _create_test_conv_state(temp_dir)
        tools = GlobTool.create(conv_state)
        tool = tools[0]

        # Test finding Python files
        action = GlobAction(pattern="**/*.py")
        assert tool.executor is not None
        assert tool.executor is not None
        observation = tool.executor(action)

        assert isinstance(observation, GlobObservation)
        assert observation.is_error is False
        assert len(observation.files) == 3  # test.py, src/app.py, tests/test_main.py
        assert observation.pattern == "**/*.py"
        assert observation.search_path == str(Path(temp_dir).resolve())
        assert not observation.truncated

        # Check that all found files are Python files
        for file_path in observation.files:
            assert file_path.endswith(".py")
            assert os.path.exists(file_path)


def test_glob_tool_specific_directory():
    """Test that GlobTool can search in specific directories."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create test files
        src_dir = Path(temp_dir) / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("# App code")
        (src_dir / "utils.py").write_text("# Utils code")

        tests_dir = Path(temp_dir) / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_app.py").write_text("# Test code")

        conv_state = _create_test_conv_state(temp_dir)
        tools = GlobTool.create(conv_state)
        tool = tools[0]

        # Test searching only in src directory
        action = GlobAction(pattern="*.py", path=str(src_dir))
        assert tool.executor is not None
        observation = tool.executor(action)

        assert observation.is_error is False
        assert len(observation.files) == 2  # app.py, utils.py
        assert observation.search_path == str(src_dir.resolve())

        # Check that all found files are in src directory
        # Use resolved path for comparison (handles Windows short paths)
        src_dir_resolved = str(src_dir.resolve())
        for file_path in observation.files:
            assert src_dir_resolved in file_path


def test_glob_tool_no_matches():
    """Test that GlobTool handles no matches gracefully."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a single text file
        (Path(temp_dir) / "readme.txt").write_text("Hello world")

        conv_state = _create_test_conv_state(temp_dir)
        tools = GlobTool.create(conv_state)
        tool = tools[0]

        # Search for Python files (should find none)
        action = GlobAction(pattern="**/*.py")
        assert tool.executor is not None
        observation = tool.executor(action)

        assert observation.is_error is False
        assert len(observation.files) == 0
        assert observation.pattern == "**/*.py"
        assert not observation.truncated


def test_glob_tool_invalid_directory():
    """Test that GlobTool handles invalid search directories."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = GlobTool.create(conv_state)
        tool = tools[0]

        # Search in non-existent directory
        action = GlobAction(pattern="*.py", path="/nonexistent/directory")
        assert tool.executor is not None
        observation = tool.executor(action)

        assert observation.is_error is True
        assert "is not a valid directory" in observation.text
        assert len(observation.files) == 0


def test_glob_tool_complex_patterns():
    """Test that GlobTool handles complex glob patterns."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create test files with various extensions
        test_files = [
            "config.json",
            "config.yaml",
            "config.yml",
            "config.toml",
            "readme.md",
            "app.py",
        ]

        for file_path in test_files:
            (Path(temp_dir) / file_path).write_text(f"Content of {file_path}")

        conv_state = _create_test_conv_state(temp_dir)
        tools = GlobTool.create(conv_state)
        tool = tools[0]

        # Test pattern for config files
        action = GlobAction(pattern="config.*")
        assert tool.executor is not None
        observation = tool.executor(action)

        assert observation.is_error is False
        assert len(observation.files) == 4  # All config files
        assert observation.pattern == "config.*"

        # Check that all found files have the expected extensions
        extensions = {Path(f).suffix for f in observation.files}
        assert extensions == {".json", ".yaml", ".yml", ".toml"}


def test_glob_tool_directories_excluded():
    """Test that GlobTool excludes directories from results."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create directories and files
        (Path(temp_dir) / "src").mkdir()
        (Path(temp_dir) / "tests").mkdir()
        (Path(temp_dir) / "app.py").write_text("# App code")
        (Path(temp_dir) / "src" / "utils.py").write_text("# Utils code")

        conv_state = _create_test_conv_state(temp_dir)
        tools = GlobTool.create(conv_state)
        tool = tools[0]

        # Search for everything
        action = GlobAction(pattern="*")
        assert tool.executor is not None
        observation = tool.executor(action)

        assert observation.is_error is False
        # Should find all files recursively, but not directories
        assert len(observation.files) == 2  # app.py and src/utils.py
        # Verify both files are present
        file_names = [Path(f).name for f in observation.files]
        assert "app.py" in file_names
        assert "utils.py" in file_names
        # Verify no directory paths are included
        for file_path in observation.files:
            assert Path(file_path).is_file() or not Path(file_path).exists()


def test_glob_tool_to_llm_content():
    """Test that GlobObservation converts to LLM content correctly."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create test files
        (Path(temp_dir) / "test1.py").write_text("# Test 1")
        (Path(temp_dir) / "test2.py").write_text("# Test 2")

        conv_state = _create_test_conv_state(temp_dir)
        tools = GlobTool.create(conv_state)
        tool = tools[0]

        # Test successful search
        action = GlobAction(pattern="*.py")
        assert tool.executor is not None
        observation = tool.executor(action)

        content = observation.to_llm_content
        assert len(content) == 1
        text_content = content[0].text
        assert "Found 2 file(s) matching pattern" in text_content
        assert "*.py" in text_content
        assert "test1.py" in text_content
        assert "test2.py" in text_content


def test_glob_tool_to_llm_content_no_matches():
    """Test LLM content for no matches."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = GlobTool.create(conv_state)
        tool = tools[0]

        # Search for non-existent files
        action = GlobAction(pattern="*.nonexistent")
        assert tool.executor is not None
        observation = tool.executor(action)

        content = observation.to_llm_content
        assert len(content) == 1
        text_content = content[0].text
        assert "No files found matching pattern" in text_content
        assert "*.nonexistent" in text_content


def test_glob_tool_to_llm_content_error():
    """Test LLM content for error cases."""
    with tempfile.TemporaryDirectory() as temp_dir:
        conv_state = _create_test_conv_state(temp_dir)
        tools = GlobTool.create(conv_state)
        tool = tools[0]

        # Search in invalid directory
        action = GlobAction(pattern="*.py", path="/invalid/path")
        assert tool.executor is not None
        observation = tool.executor(action)

        content = observation.to_llm_content
        assert len(content) == 2
        assert content[0].text == GlobObservation.ERROR_MESSAGE_HEADER
        text_content = content[1].text
        assert "is not a valid directory" in text_content


def test_glob_tool_truncation():
    """Test that GlobTool truncates results when there are too many matches."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create more than 100 files
        for i in range(150):
            (Path(temp_dir) / f"file_{i:03d}.txt").write_text(f"Content {i}")

        conv_state = _create_test_conv_state(temp_dir)
        tools = GlobTool.create(conv_state)
        tool = tools[0]

        # Search for all text files
        action = GlobAction(pattern="*.txt")
        assert tool.executor is not None
        observation = tool.executor(action)

        assert observation.is_error is False
        assert len(observation.files) == 100  # Truncated to 100
        assert observation.truncated is True

        # Check LLM content mentions truncation
        content = observation.to_llm_content
        text_content = content[0].text
        assert "Results truncated to first 100 files" in text_content
