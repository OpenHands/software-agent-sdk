"""Tests for GitTool schema and definition."""

from openhands.sdk.tool import get_tool_metadata
from openhands.tools.git import GitAction, GitObservation, GitTool


def test_git_tool_registration():
    """Test that GitTool is properly registered."""
    assert GitTool is not None
    assert GitTool.name == "git"


def test_git_action_schema():
    """Test GitAction schema."""
    # Test basic status action
    action = GitAction(command="status")
    assert action.command == "status"

    # Test add action with files
    action = GitAction(command="add", files=["test.txt"])
    assert action.command == "add"
    assert action.files == ["test.txt"]

    # Test commit action
    action = GitAction(command="commit", message="Test commit")
    assert action.command == "commit"
    assert action.message == "Test commit"

    # Test branch action
    action = GitAction(command="branch", branch_name="feature")
    assert action.command == "branch"
    assert action.branch_name == "feature"


def test_git_observation_schema():
    """Test GitObservation schema."""
    obs = GitObservation.from_text(
        text="Test output",
        command="status",
        success=True,
    )
    assert obs.command == "status"
    assert obs.success is True
    assert obs.text == "Test output"
    assert obs.is_error is False


def test_git_observation_error():
    """Test GitObservation error state."""
    obs = GitObservation.from_text(
        text="Error message",
        command="push",
        is_error=True,
        success=False,
    )
    assert obs.command == "push"
    assert obs.success is False
    assert obs.is_error is True
    assert obs.text == "Error message"


def test_git_tool_metadata():
    """Test GitTool metadata."""
    metadata = get_tool_metadata(GitTool)
    assert metadata.name == "git"
    assert "git operations" in metadata.system_annotations.lower()


def test_git_action_visualize():
    """Test GitAction visualization."""
    # Test status command
    action = GitAction(command="status")
    viz = action.visualize
    assert "git" in viz.plain.lower()
    assert "status" in viz.plain.lower()

    # Test commit with message
    action = GitAction(command="commit", message="Test commit")
    viz = action.visualize
    assert "git" in viz.plain.lower()
    assert "commit" in viz.plain.lower()


def test_git_observation_visualize():
    """Test GitObservation visualization."""
    obs = GitObservation.from_text(
        text="Success",
        command="status",
        success=True,
    )
    viz = obs.visualize
    assert "git" in viz.plain.lower()

    # Test error visualization
    obs = GitObservation.from_text(
        text="Error",
        command="push",
        is_error=True,
        success=False,
    )
    viz = obs.visualize
    # Error marker should be present
    assert "❌" in viz.plain or "error" in viz.plain.lower()


def test_git_action_defaults():
    """Test GitAction default values."""
    action = GitAction(command="status")
    assert action.remote == "origin"
    assert action.force is False
    assert action.create_branch is False
    assert action.all_changes is False
    assert action.max_count == 10
    assert action.oneline is False
    assert action.reset_mode == "mixed"
    assert action.reset_target == "HEAD"
    assert action.stash_operation == "save"
    assert action.remote_operation == "list"


def test_git_action_with_repo_path():
    """Test GitAction with custom repo_path."""
    action = GitAction(command="status", repo_path="/path/to/repo")
    assert action.repo_path == "/path/to/repo"


def test_git_action_push_parameters():
    """Test GitAction push command parameters."""
    action = GitAction(
        command="push",
        remote="upstream",
        branch_name="main",
        force=True,
    )
    assert action.command == "push"
    assert action.remote == "upstream"
    assert action.branch_name == "main"
    assert action.force is True


def test_git_action_log_parameters():
    """Test GitAction log command parameters."""
    action = GitAction(
        command="log",
        max_count=20,
        oneline=True,
    )
    assert action.command == "log"
    assert action.max_count == 20
    assert action.oneline is True


def test_git_action_diff_parameters():
    """Test GitAction diff command parameters."""
    action = GitAction(
        command="diff",
        ref_1="HEAD",
        ref_2="HEAD~1",
        path_filter="src/",
    )
    assert action.command == "diff"
    assert action.ref_1 == "HEAD"
    assert action.ref_2 == "HEAD~1"
    assert action.path_filter == "src/"


def test_git_action_stash_parameters():
    """Test GitAction stash command parameters."""
    action = GitAction(
        command="stash",
        stash_operation="save",
        stash_message="WIP: new feature",
    )
    assert action.command == "stash"
    assert action.stash_operation == "save"
    assert action.stash_message == "WIP: new feature"


def test_git_action_remote_parameters():
    """Test GitAction remote command parameters."""
    action = GitAction(
        command="remote",
        remote_operation="add",
        remote_name="upstream",
        remote_url="https://github.com/example/repo.git",
    )
    assert action.command == "remote"
    assert action.remote_operation == "add"
    assert action.remote_name == "upstream"
    assert action.remote_url == "https://github.com/example/repo.git"


def test_git_action_clone_parameters():
    """Test GitAction clone command parameters."""
    action = GitAction(
        command="clone",
        url="https://github.com/example/repo.git",
        repo_path="./repo",
    )
    assert action.command == "clone"
    assert action.url == "https://github.com/example/repo.git"
    assert action.repo_path == "./repo"
