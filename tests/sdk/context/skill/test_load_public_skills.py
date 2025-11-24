"""Tests for load_public_skills functionality."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from httpx import Response

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.context.skills import (
    KeywordTrigger,
    Skill,
    load_public_skills,
)


@pytest.fixture
def mock_github_api_response():
    """Create a mock GitHub API tree response for skills/ subdirectory."""
    return {
        "tree": [
            {"path": "README.md", "type": "blob"},
            {"path": "git.md", "type": "blob"},
            {"path": "docker.md", "type": "blob"},
            {"path": "testing.md", "type": "blob"},
        ]
    }


@pytest.fixture
def mock_skill_content():
    """Create mock skill file content."""
    return {
        "git.md": (
            "---\n"
            "name: git\n"
            "triggers:\n"
            "  - git\n"
            "  - github\n"
            "---\n"
            "Git best practices and commands."
        ),
        "docker.md": (
            "---\n"
            "name: docker\n"
            "triggers:\n"
            "  - docker\n"
            "  - container\n"
            "---\n"
            "Docker guidelines and commands."
        ),
        "testing.md": "---\nname: testing\n---\nTesting guidelines for all repos.",
    }


def test_load_public_skills_success(mock_github_api_response, mock_skill_content):
    """Test successfully loading skills from public repository."""

    def mock_get(url, *args, **kwargs):
        if "git/trees" in url:
            return Response(
                200,
                json=mock_github_api_response,
                request=httpx.Request("GET", url),
            )
        elif url.endswith("skills/git.md"):
            return Response(
                200,
                text=mock_skill_content["git.md"],
                request=httpx.Request("GET", url),
            )
        elif url.endswith("skills/docker.md"):
            return Response(
                200,
                text=mock_skill_content["docker.md"],
                request=httpx.Request("GET", url),
            )
        elif url.endswith("skills/testing.md"):
            return Response(
                200,
                text=mock_skill_content["testing.md"],
                request=httpx.Request("GET", url),
            )
        raise httpx.HTTPError(f"Unexpected URL: {url}")

    mock_client = MagicMock()
    mock_client.get = mock_get
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("httpx.Client", return_value=mock_client):
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


def test_load_public_skills_http_error():
    """Test handling of HTTP errors when fetching from repository."""

    def mock_get(url, *args, **kwargs):
        response = Response(
            404,
            json={"message": "Not Found"},
            request=httpx.Request("GET", url),
        )
        raise httpx.HTTPStatusError(
            "Not Found", request=response.request, response=response
        )

    mock_client = MagicMock()
    mock_client.get = mock_get
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("httpx.Client", return_value=mock_client):
        skills = load_public_skills()
        assert skills == []


def test_load_public_skills_network_error():
    """Test handling of network errors."""

    def mock_get(url, *args, **kwargs):
        raise httpx.RequestError("Network error", request=httpx.Request("GET", url))

    mock_client = MagicMock()
    mock_client.get = mock_get
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("httpx.Client", return_value=mock_client):
        skills = load_public_skills()
        assert skills == []


def test_load_public_skills_invalid_url():
    """Test handling of invalid repository URL."""
    skills = load_public_skills(repo_url="https://invalid-url.com/repo")
    assert skills == []


def test_load_public_skills_custom_repo():
    """Test loading from a custom repository URL."""
    custom_repo = "https://github.com/custom-org/custom-skills"

    def mock_get(url, *args, **kwargs):
        assert "custom-org/custom-skills" in url
        if "git/trees" in url:
            return Response(
                200,
                json={"tree": [{"path": "custom.md", "type": "blob"}]},
                request=httpx.Request("GET", url),
            )
        elif url.endswith("skills/custom.md"):
            return Response(
                200,
                text="---\nname: custom\n---\nCustom skill content.",
                request=httpx.Request("GET", url),
            )
        raise httpx.HTTPError(f"Unexpected URL: {url}")

    mock_client = MagicMock()
    mock_client.get = mock_get
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("httpx.Client", return_value=mock_client):
        skills = load_public_skills(repo_url=custom_repo)
        assert len(skills) == 1
        assert skills[0].name == "custom"


def test_load_public_skills_custom_branch(mock_github_api_response, mock_skill_content):
    """Test loading from a specific branch."""
    custom_branch = "develop"

    def mock_get(url, *args, **kwargs):
        assert custom_branch in url
        if "git/trees" in url:
            return Response(
                200,
                json=mock_github_api_response,
                request=httpx.Request("GET", url),
            )
        elif url.endswith("skills/git.md"):
            return Response(
                200,
                text=mock_skill_content["git.md"],
                request=httpx.Request("GET", url),
            )
        elif url.endswith("skills/docker.md"):
            return Response(
                200,
                text=mock_skill_content["docker.md"],
                request=httpx.Request("GET", url),
            )
        elif url.endswith("skills/testing.md"):
            return Response(
                200,
                text=mock_skill_content["testing.md"],
                request=httpx.Request("GET", url),
            )
        raise httpx.HTTPError(f"Unexpected URL: {url}")

    mock_client = MagicMock()
    mock_client.get = mock_get
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("httpx.Client", return_value=mock_client):
        skills = load_public_skills(branch=custom_branch)
        assert len(skills) == 3


def test_load_public_skills_with_invalid_skill():
    """Test that invalid skills are skipped gracefully."""
    call_count = {"valid": 0, "invalid": 0}

    def mock_get(url, *args, **kwargs):
        if "git/trees" in url:
            return Response(
                200,
                json={
                    "tree": [
                        {"path": "valid.md", "type": "blob"},
                        {"path": "invalid.md", "type": "blob"},
                    ]
                },
                request=httpx.Request("GET", url),
            )
        elif url.endswith("skills/valid.md"):
            call_count["valid"] += 1
            return Response(
                200,
                text="---\nname: valid\n---\nValid skill content.",
                request=httpx.Request("GET", url),
            )
        elif url.endswith("skills/invalid.md"):
            call_count["invalid"] += 1
            # Invalid: triggers must be a list, not a string
            return Response(
                200,
                text="---\nname: invalid\ntriggers: not_a_list\n---\nInvalid skill.",
                request=httpx.Request("GET", url),
            )
        raise httpx.HTTPError(f"Unexpected URL: {url}")

    mock_client = MagicMock()
    mock_client.get = mock_get
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("httpx.Client", return_value=mock_client):
        skills = load_public_skills()
        # Only valid skill should be loaded, invalid one skipped
        assert len(skills) == 1, (
            f"Expected 1 skill but got {len(skills)}: {[s.name for s in skills]}"
        )
        assert skills[0].name == "valid"
        assert call_count["valid"] == 1
        assert call_count["invalid"] == 1  # Was called but failed to load


def test_agent_context_loads_public_skills(
    mock_github_api_response, mock_skill_content
):
    """Test that AgentContext loads public skills when enabled."""

    def mock_get(url, *args, **kwargs):
        if "git/trees" in url:
            return Response(
                200,
                json=mock_github_api_response,
                request=httpx.Request("GET", url),
            )
        elif url.endswith("skills/git.md"):
            return Response(
                200,
                text=mock_skill_content["git.md"],
                request=httpx.Request("GET", url),
            )
        elif url.endswith("skills/docker.md"):
            return Response(
                200,
                text=mock_skill_content["docker.md"],
                request=httpx.Request("GET", url),
            )
        elif url.endswith("skills/testing.md"):
            return Response(
                200,
                text=mock_skill_content["testing.md"],
                request=httpx.Request("GET", url),
            )
        raise httpx.HTTPError(f"Unexpected URL: {url}")

    mock_client = MagicMock()
    mock_client.get = mock_get
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("httpx.Client", return_value=mock_client):
        context = AgentContext(load_public_skills=True)
        skill_names = {s.name for s in context.skills}
        assert "git" in skill_names
        assert "docker" in skill_names
        assert "testing" in skill_names


def test_agent_context_can_disable_public_skills_loading():
    """Test that public skills loading can be disabled."""
    context = AgentContext(load_public_skills=False)
    assert context.skills == []


def test_agent_context_merges_explicit_and_public_skills(
    mock_github_api_response, mock_skill_content
):
    """Test that explicit skills and public skills are merged correctly."""

    def mock_get(url, *args, **kwargs):
        if "git/trees" in url:
            return Response(
                200,
                json=mock_github_api_response,
                request=httpx.Request("GET", url),
            )
        elif url.endswith("skills/git.md"):
            return Response(
                200,
                text=mock_skill_content["git.md"],
                request=httpx.Request("GET", url),
            )
        elif url.endswith("skills/docker.md"):
            return Response(
                200,
                text=mock_skill_content["docker.md"],
                request=httpx.Request("GET", url),
            )
        elif url.endswith("skills/testing.md"):
            return Response(
                200,
                text=mock_skill_content["testing.md"],
                request=httpx.Request("GET", url),
            )
        raise httpx.HTTPError(f"Unexpected URL: {url}")

    mock_client = MagicMock()
    mock_client.get = mock_get
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    # Create explicit skill
    explicit_skill = Skill(
        name="explicit_skill",
        content="Explicit skill content.",
        trigger=None,
    )

    with patch("httpx.Client", return_value=mock_client):
        context = AgentContext(skills=[explicit_skill], load_public_skills=True)
        skill_names = {s.name for s in context.skills}
        assert "explicit_skill" in skill_names
        assert "git" in skill_names
        assert len(context.skills) == 4  # 1 explicit + 3 public


def test_agent_context_explicit_skill_takes_precedence(
    mock_github_api_response, mock_skill_content
):
    """Test that explicitly provided skills take precedence over public skills."""

    def mock_get(url, *args, **kwargs):
        if "git/trees" in url:
            return Response(
                200,
                json=mock_github_api_response,
                request=httpx.Request("GET", url),
            )
        elif url.endswith("skills/git.md"):
            return Response(
                200,
                text=mock_skill_content["git.md"],
                request=httpx.Request("GET", url),
            )
        elif url.endswith("skills/docker.md"):
            return Response(
                200,
                text=mock_skill_content["docker.md"],
                request=httpx.Request("GET", url),
            )
        elif url.endswith("skills/testing.md"):
            return Response(
                200,
                text=mock_skill_content["testing.md"],
                request=httpx.Request("GET", url),
            )
        raise httpx.HTTPError(f"Unexpected URL: {url}")

    mock_client = MagicMock()
    mock_client.get = mock_get
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    # Create explicit skill with same name as public skill
    explicit_skill = Skill(
        name="git",
        content="Explicit git skill content.",
        trigger=None,
    )

    with patch("httpx.Client", return_value=mock_client):
        context = AgentContext(skills=[explicit_skill], load_public_skills=True)
        # Should have 3 skills (1 explicit git + 2 other public skills)
        assert len(context.skills) == 3
        git_skill = next(s for s in context.skills if s.name == "git")
        # Explicit skill should be used, not the public skill
        assert git_skill.content == "Explicit git skill content."
