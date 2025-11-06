"""Tests for agent_server docker build module."""

import os
from unittest.mock import patch


def test_git_info_priority_sdk_sha():
    """Test that SDK_SHA takes priority over GITHUB_SHA and git commands."""
    from openhands.agent_server.docker.build import _git_info

    with patch.dict(
        os.environ,
        {
            "SDK_SHA": "abc1234567890",
            "GITHUB_SHA": "def1234567890",
            "SDK_REF": "refs/heads/test-branch",  # Also set REF to avoid git call
        },
        clear=False,
    ):
        with patch(
            "openhands.agent_server.docker.build._run"
        ) as mock_run:  # Should not be called
            git_ref, git_sha, short_sha = _git_info()

            assert git_sha == "abc1234567890"
            assert short_sha == "abc1234"
            # git command should not be called when SDK_SHA is set
            mock_run.assert_not_called()


def test_git_info_priority_github_sha():
    """Test that GITHUB_SHA is used when SDK_SHA is not set."""
    from openhands.agent_server.docker.build import _git_info

    with patch.dict(
        os.environ,
        {
            "GITHUB_SHA": "def1234567890",
            "GITHUB_REF": "refs/heads/main",  # Also set REF to avoid git call
        },
        clear=False,
    ):
        # Remove SDK_SHA if it exists
        if "SDK_SHA" in os.environ:
            del os.environ["SDK_SHA"]
        if "SDK_REF" in os.environ:
            del os.environ["SDK_REF"]

        with patch(
            "openhands.agent_server.docker.build._run"
        ) as mock_run:  # Should not be called
            git_ref, git_sha, short_sha = _git_info()

            assert git_sha == "def1234567890"
            assert short_sha == "def1234"
            mock_run.assert_not_called()


def test_git_info_priority_sdk_ref():
    """Test that SDK_REF takes priority over GITHUB_REF and git commands."""
    from openhands.agent_server.docker.build import _git_info

    with patch.dict(
        os.environ,
        {
            "SDK_REF": "refs/heads/my-branch",
            "GITHUB_REF": "refs/heads/other-branch",
            "SDK_SHA": "test123456",  # Also set SHA to avoid git call
        },
        clear=False,
    ):
        git_ref, git_sha, short_sha = _git_info()

        assert git_ref == "refs/heads/my-branch"


def test_git_info_priority_github_ref():
    """Test that GITHUB_REF is used when SDK_REF is not set."""
    from openhands.agent_server.docker.build import _git_info

    with patch.dict(
        os.environ,
        {
            "GITHUB_REF": "refs/heads/other-branch",
            "GITHUB_SHA": "test123456",  # Also set SHA to avoid git call
        },
        clear=False,
    ):
        # Remove SDK_REF if it exists
        if "SDK_REF" in os.environ:
            del os.environ["SDK_REF"]
        if "SDK_SHA" in os.environ:
            del os.environ["SDK_SHA"]

        git_ref, git_sha, short_sha = _git_info()

        assert git_ref == "refs/heads/other-branch"


def test_git_info_submodule_scenario():
    """
    Test the submodule scenario where parent repo sets SDK_SHA and SDK_REF.
    This simulates the use case from the PR description.
    """
    from openhands.agent_server.docker.build import _git_info

    # Simulate parent repo extracting submodule commit and passing it
    with patch.dict(
        os.environ,
        {
            "SDK_SHA": "a612c0a1234567890abcdef",  # Submodule commit
            "SDK_REF": "refs/heads/detached",  # Detached HEAD in submodule
        },
        clear=False,
    ):
        git_ref, git_sha, short_sha = _git_info()

        assert git_sha == "a612c0a1234567890abcdef"
        assert short_sha == "a612c0a"
        assert git_ref == "refs/heads/detached"


def test_git_info_empty_sdk_sha_falls_back():
    """Test that empty SDK_SHA falls back to GITHUB_SHA."""
    from openhands.agent_server.docker.build import _git_info

    with patch.dict(
        os.environ,
        {
            "SDK_SHA": "",  # Empty string should fall back
            "GITHUB_SHA": "github123456",
            "GITHUB_REF": "refs/heads/fallback",  # Also set REF to avoid git call
        },
        clear=False,
    ):
        with patch("openhands.agent_server.docker.build._run") as mock_run:
            git_ref, git_sha, short_sha = _git_info()

            assert git_sha == "github123456"
            assert short_sha == "github1"
            mock_run.assert_not_called()
