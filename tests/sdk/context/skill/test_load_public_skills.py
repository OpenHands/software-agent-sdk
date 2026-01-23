"""Tests for load_public_skills functionality with git-based caching."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.context.skills import (
    KeywordTrigger,
    Skill,
    load_public_skills,
)
from openhands.sdk.context.skills.utils import update_skills_repository


@pytest.fixture
def mock_repo_dir(tmp_path):
    """Create a mock git repository with skills."""
    repo_dir = tmp_path / "mock_repo"
    repo_dir.mkdir()

    # Create skills directory
    skills_dir = repo_dir / "skills"
    skills_dir.mkdir()

    # Create skill files
    git_skill = skills_dir / "git.md"
    git_skill.write_text(
        "---\n"
        "name: git\n"
        "triggers:\n"
        "  - git\n"
        "  - github\n"
        "---\n"
        "Git best practices and commands."
    )

    docker_skill = skills_dir / "docker.md"
    docker_skill.write_text(
        "---\n"
        "name: docker\n"
        "triggers:\n"
        "  - docker\n"
        "  - container\n"
        "---\n"
        "Docker guidelines and commands."
    )

    testing_skill = skills_dir / "testing.md"
    testing_skill.write_text(
        "---\nname: testing\n---\nTesting guidelines for all repos."
    )

    # Create .git directory to simulate a git repo
    git_dir = repo_dir / ".git"
    git_dir.mkdir()

    return repo_dir


def test_load_public_skills_success(mock_repo_dir, tmp_path):
    """Test successfully loading skills from cached repository."""

    def mock_update_repo(repo_url, branch, cache_dir):
        return mock_repo_dir

    with (
        patch(
            "openhands.sdk.context.skills.skill.update_skills_repository",
            side_effect=mock_update_repo,
        ),
        patch(
            "openhands.sdk.context.skills.skill.get_skills_cache_dir",
            return_value=tmp_path,
        ),
    ):
        skills = load_public_skills()
        assert len(skills) == 3
        skill_names = {s.name for s in skills}
        assert skill_names == {"git", "docker", "testing"}

        # Check git skill details
        git_skill = next(s for s in skills if s.name == "git")
        assert isinstance(git_skill.trigger, KeywordTrigger)
        assert "git" in git_skill.trigger.keywords

        # Check testing skill (no trigger - always active)
        testing_skill = next(s for s in skills if s.name == "testing")
        assert testing_skill.trigger is None


def test_load_public_skills_repo_update_fails(tmp_path):
    """Test handling when repository update fails."""

    def mock_update_repo(repo_url, branch, cache_dir):
        return None

    with (
        patch(
            "openhands.sdk.context.skills.skill.update_skills_repository",
            side_effect=mock_update_repo,
        ),
        patch(
            "openhands.sdk.context.skills.skill.get_skills_cache_dir",
            return_value=tmp_path,
        ),
    ):
        skills = load_public_skills()
        assert skills == []


def test_load_public_skills_no_skills_directory(tmp_path):
    """Test handling when skills directory doesn't exist in repo."""
    repo_dir = tmp_path / "mock_repo"
    repo_dir.mkdir()
    # No skills directory created

    def mock_update_repo(repo_url, branch, cache_dir):
        return repo_dir

    with (
        patch(
            "openhands.sdk.context.skills.skill.update_skills_repository",
            side_effect=mock_update_repo,
        ),
        patch(
            "openhands.sdk.context.skills.skill.get_skills_cache_dir",
            return_value=tmp_path,
        ),
    ):
        skills = load_public_skills()
        assert skills == []


def test_load_public_skills_with_invalid_skill(tmp_path):
    """Test that invalid skills are skipped gracefully."""
    repo_dir = tmp_path / "mock_repo"
    repo_dir.mkdir()
    skills_dir = repo_dir / "skills"
    skills_dir.mkdir()

    # Valid skill
    valid_skill = skills_dir / "valid.md"
    valid_skill.write_text("---\nname: valid\n---\nValid skill content.")

    # Invalid skill
    invalid_skill = skills_dir / "invalid.md"
    invalid_skill.write_text(
        "---\nname: invalid\ntriggers: not_a_list\n---\nInvalid skill."
    )

    def mock_update_repo(repo_url, branch, cache_dir):
        return repo_dir

    with (
        patch(
            "openhands.sdk.context.skills.skill.update_skills_repository",
            side_effect=mock_update_repo,
        ),
        patch(
            "openhands.sdk.context.skills.skill.get_skills_cache_dir",
            return_value=tmp_path,
        ),
    ):
        skills = load_public_skills()
        # Only valid skill should be loaded, invalid one skipped
        assert len(skills) == 1
        assert skills[0].name == "valid"


def test_update_skills_repository_clone_new(tmp_path):
    """Test cloning a new repository."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    mock_result = MagicMock()
    mock_result.returncode = 0

    with patch(
        "openhands.sdk.git.utils.subprocess.run", return_value=mock_result
    ) as mock_run:
        repo_path = update_skills_repository(
            "https://github.com/OpenHands/skills",
            "main",
            cache_dir,
        )

        assert repo_path is not None
        # Check that git clone was called
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0][0] == "git"
        assert call_args[0][0][1] == "clone"
        assert "--branch" in call_args[0][0]
        assert "main" in call_args[0][0]


def test_update_skills_repository_update_existing(tmp_path):
    """Test updating an existing repository."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # Create existing repo with .git directory
    repo_path = cache_dir / "public-skills"
    repo_path.mkdir()
    git_dir = repo_path / ".git"
    git_dir.mkdir()

    mock_result = MagicMock()
    mock_result.returncode = 0
    # Simulate being on a branch (not detached HEAD) so reset is called
    mock_result.stdout = "main"

    with patch(
        "openhands.sdk.git.utils.subprocess.run", return_value=mock_result
    ) as mock_run:
        result_path = update_skills_repository(
            "https://github.com/OpenHands/skills",
            "main",
            cache_dir,
        )

        assert result_path == repo_path
        # The git operations are: fetch, checkout, get_current_branch, reset
        # (get_current_branch returns branch name so reset is called)
        assert mock_run.call_count == 4
        all_commands = [call[0][0] for call in mock_run.call_args_list]
        assert all_commands[0][:3] == ["git", "fetch", "origin"]
        assert all_commands[1][:2] == ["git", "checkout"]
        assert all_commands[2] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]
        assert all_commands[3][:3] == ["git", "reset", "--hard"]


def test_update_skills_repository_clone_timeout(tmp_path):
    """Test handling of timeout during clone."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    with patch(
        "openhands.sdk.git.utils.subprocess.run",
        side_effect=subprocess.TimeoutExpired("git", 60),
    ) as mock_run:
        repo_path = update_skills_repository(
            "https://github.com/OpenHands/skills",
            "main",
            cache_dir,
        )

        assert repo_path is None
        mock_run.assert_called_once()


def test_update_skills_repository_update_fails_uses_cache(tmp_path):
    """Test that existing cache is used when update fails."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # Create existing repo with .git directory
    repo_path = cache_dir / "public-skills"
    repo_path.mkdir()
    git_dir = repo_path / ".git"
    git_dir.mkdir()

    # Mock subprocess.run to return a failed result (non-zero return code)
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "Error: fetch failed"

    with patch(
        "openhands.sdk.git.utils.subprocess.run",
        return_value=mock_result,
    ):
        result_path = update_skills_repository(
            "https://github.com/OpenHands/skills",
            "main",
            cache_dir,
        )

        # Should still return the cached path even though update failed
        assert result_path == repo_path


def test_agent_context_loads_public_skills(mock_repo_dir, tmp_path):
    """Test that AgentContext loads public skills when enabled."""

    def mock_update_repo(repo_url, branch, cache_dir):
        return mock_repo_dir

    with (
        patch(
            "openhands.sdk.context.skills.skill.update_skills_repository",
            side_effect=mock_update_repo,
        ),
        patch(
            "openhands.sdk.context.skills.skill.get_skills_cache_dir",
            return_value=tmp_path,
        ),
    ):
        context = AgentContext(load_public_skills=True)
        skill_names = {s.name for s in context.skills}
        assert "git" in skill_names
        assert "docker" in skill_names
        assert "testing" in skill_names


def test_agent_context_can_disable_public_skills_loading():
    """Test that public skills loading can be disabled."""
    context = AgentContext(load_public_skills=False)
    assert context.skills == []


def test_agent_context_merges_explicit_and_public_skills(mock_repo_dir, tmp_path):
    """Test that explicit skills and public skills are merged correctly."""

    def mock_update_repo(repo_url, branch, cache_dir):
        return mock_repo_dir

    # Create explicit skill
    explicit_skill = Skill(
        name="explicit_skill",
        content="Explicit skill content.",
        trigger=None,
    )

    with (
        patch(
            "openhands.sdk.context.skills.skill.update_skills_repository",
            side_effect=mock_update_repo,
        ),
        patch(
            "openhands.sdk.context.skills.skill.get_skills_cache_dir",
            return_value=tmp_path,
        ),
    ):
        context = AgentContext(skills=[explicit_skill], load_public_skills=True)
        skill_names = {s.name for s in context.skills}
        assert "explicit_skill" in skill_names
        assert "git" in skill_names
        assert len(context.skills) == 4  # 1 explicit + 3 public


def test_agent_context_explicit_skill_takes_precedence(mock_repo_dir, tmp_path):
    """Test that explicitly provided skills take precedence over public skills."""

    def mock_update_repo(repo_url, branch, cache_dir):
        return mock_repo_dir

    # Create explicit skill with same name as public skill
    explicit_skill = Skill(
        name="git",
        content="Explicit git skill content.",
        trigger=None,
    )

    with (
        patch(
            "openhands.sdk.context.skills.skill.update_skills_repository",
            side_effect=mock_update_repo,
        ),
        patch(
            "openhands.sdk.context.skills.skill.get_skills_cache_dir",
            return_value=tmp_path,
        ),
    ):
        context = AgentContext(skills=[explicit_skill], load_public_skills=True)
        # Should have 3 skills (1 explicit git + 2 other public skills)
        assert len(context.skills) == 3
        git_skill = next(s for s in context.skills if s.name == "git")
        # Explicit skill should be used, not the public skill
        assert git_skill.content == "Explicit git skill content."


def test_load_public_skills_custom_repo(mock_repo_dir, tmp_path):
    """Test loading from a custom repository URL."""

    def mock_update_repo(repo_url, branch, cache_dir):
        assert repo_url == "https://github.com/custom-org/custom-skills"
        return mock_repo_dir

    with (
        patch(
            "openhands.sdk.context.skills.skill.update_skills_repository",
            side_effect=mock_update_repo,
        ),
        patch(
            "openhands.sdk.context.skills.skill.get_skills_cache_dir",
            return_value=tmp_path,
        ),
    ):
        skills = load_public_skills(
            repo_url="https://github.com/custom-org/custom-skills"
        )
        assert len(skills) == 3


def test_load_public_skills_custom_branch(mock_repo_dir, tmp_path):
    """Test loading from a specific branch."""

    def mock_update_repo(repo_url, branch, cache_dir):
        assert branch == "develop"
        return mock_repo_dir

    with (
        patch(
            "openhands.sdk.context.skills.skill.update_skills_repository",
            side_effect=mock_update_repo,
        ),
        patch(
            "openhands.sdk.context.skills.skill.get_skills_cache_dir",
            return_value=tmp_path,
        ),
    ):
        skills = load_public_skills(branch="develop")
        assert len(skills) == 3
