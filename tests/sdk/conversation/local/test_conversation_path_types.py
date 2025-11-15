"""Test Path type handling in Conversation and LocalConversation."""

import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import LocalWorkspace


def create_test_agent() -> Agent:
    """Create a test agent."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    return Agent(llm=llm, tools=[])


def test_conversation_with_path_workspace():
    """Test that Path objects can be passed as workspace parameter."""
    agent = create_test_agent()

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace_path = Path(tmpdir) / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)

        # Should accept Path object for workspace
        conv = Conversation(agent=agent, workspace=workspace_path)

        # Verify workspace is set correctly
        assert conv.workspace is not None
        assert isinstance(conv.workspace, LocalWorkspace)
        # The working_dir should be a string representation of the path
        assert conv.workspace.working_dir == str(workspace_path)
        # Verify the path exists and is accessible
        assert Path(conv.workspace.working_dir).exists()


def test_conversation_with_path_persistence_dir():
    """Test that Path objects can be passed as persistence_dir parameter."""
    agent = create_test_agent()

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace_path = Path(tmpdir) / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)
        persistence_path = Path(tmpdir) / "persistence"
        persistence_path.mkdir(parents=True, exist_ok=True)

        # Should accept Path object for persistence_dir
        conv = Conversation(
            agent=agent,
            workspace=str(workspace_path),
            persistence_dir=persistence_path,
        )

        # Verify persistence directory is set correctly
        assert conv.state is not None
        assert conv.state.persistence_dir is not None
        # The persistence directory should include the conversation ID as a subdirectory
        expected_persistence_dir = persistence_path / conv.id.hex
        # Verify the actual persistence path matches expected
        assert Path(conv.state.persistence_dir) == expected_persistence_dir


def test_conversation_with_both_path_types():
    """Test that both workspace and persistence_dir can be Path objects."""
    agent = create_test_agent()

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace_path = Path(tmpdir) / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)
        persistence_path = Path(tmpdir) / "persistence"
        persistence_path.mkdir(parents=True, exist_ok=True)

        # Should accept Path objects for both parameters
        conv = Conversation(
            agent=agent,
            workspace=workspace_path,
            persistence_dir=persistence_path,
        )

        # Verify both are set correctly
        assert conv.workspace is not None
        assert conv.workspace.working_dir == str(workspace_path)
        assert Path(conv.workspace.working_dir).exists()

        # Verify persistence directory
        assert conv.state.persistence_dir is not None
        expected_persistence_dir = persistence_path / conv.id.hex
        assert Path(conv.state.persistence_dir) == expected_persistence_dir


def test_local_workspace_with_path():
    """Test that LocalWorkspace can be initialized with Path object."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace_path = Path(tmpdir) / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)

        # Should accept Path object directly (converted to str by validator)
        workspace = LocalWorkspace(working_dir=workspace_path)

        # Verify the working_dir is properly converted to string
        assert workspace.working_dir == str(workspace_path)
        assert isinstance(workspace.working_dir, str)


def test_conversation_with_localworkspace_from_path():
    """Test passing LocalWorkspace initialized with Path to Conversation."""
    agent = create_test_agent()

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace_path = Path(tmpdir) / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)

        # Create LocalWorkspace with Path (converted to str by validator)
        workspace = LocalWorkspace(working_dir=str(workspace_path))

        # Pass LocalWorkspace to Conversation
        conv = Conversation(agent=agent, workspace=workspace)

        # Verify workspace is correctly set
        assert conv.workspace is workspace
        assert conv.workspace.working_dir == str(workspace_path)
