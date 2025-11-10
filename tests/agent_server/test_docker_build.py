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
            git_ref, git_sha = _git_info()

            assert git_sha == "abc1234567890"
            assert git_sha[:7] == "abc1234"
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
            git_ref, git_sha = _git_info()

            assert git_sha == "def1234567890"
            assert git_sha[:7] == "def1234"
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
        git_ref, git_sha = _git_info()

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

        git_ref, git_sha = _git_info()

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
        git_ref, git_sha = _git_info()

        assert git_sha == "a612c0a1234567890abcdef"
        assert git_sha[:7] == "a612c0a"
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
            git_ref, git_sha = _git_info()

            assert git_sha == "github123456"
            assert git_sha[:7] == "github1"
            mock_run.assert_not_called()


def test_base_slug_short_image():
    """Test that short image names are returned unchanged."""
    from openhands.agent_server.docker.build import _base_slug

    # Simple image name, no truncation needed
    result = _base_slug("python:3.12")
    assert result == "python_tag_3.12"

    # With registry
    result = _base_slug("ghcr.io/org/repo:v1.0")
    assert result == "ghcr.io_s_org_s_repo_tag_v1.0"


def test_base_slug_no_tag():
    """Test base_slug with image that has no tag."""
    from openhands.agent_server.docker.build import _base_slug

    result = _base_slug("python")
    assert result == "python"

    result = _base_slug("ghcr.io/org/repo")
    assert result == "ghcr.io_s_org_s_repo"


def test_base_slug_truncation_with_tag():
    """Test that long image names with tags are truncated correctly."""
    from openhands.agent_server.docker.build import _base_slug

    # Create a very long image name that exceeds max_len=64
    long_image = (
        "ghcr.io/very-long-organization-name/"
        "very-long-repository-name:very-long-tag-v1.2.3-alpha.1+build.123"
    )

    result = _base_slug(long_image, max_len=64)

    # Check that result is within max_len
    assert len(result) <= 64

    # Check that result contains a digest suffix (13 chars: "-" + 12 hex chars)
    assert result[-13:-12] == "-"
    assert all(c in "0123456789abcdef" for c in result[-12:])

    # Check that result contains identifiable parts (repo name and/or tag)
    # The function should keep "very-long-repository-name_tag_very-long-tag-v1.2.3..."
    assert "very-long-repository-name" in result or "very-long-tag" in result


def test_base_slug_truncation_no_tag():
    """Test that long image names without tags are truncated correctly."""
    from openhands.agent_server.docker.build import _base_slug

    # Create a very long image name without a tag
    long_image = (
        "ghcr.io/very-long-organization-name-here/"
        "very-long-repository-name-that-exceeds-max-length"
    )

    result = _base_slug(long_image, max_len=64)

    # Check that result is within max_len
    assert len(result) <= 64

    # Check that result contains a digest suffix
    assert result[-13:-12] == "-"
    assert all(c in "0123456789abcdef" for c in result[-12:])

    # Check that result contains the repository name (last path segment)
    assert "very-long-repository-name" in result


def test_base_slug_custom_max_len():
    """Test base_slug with custom max_len parameter."""
    from openhands.agent_server.docker.build import _base_slug

    image = "ghcr.io/org/very-long-repository-name:v1.2.3"

    # With max_len=40, should trigger truncation
    result = _base_slug(image, max_len=40)
    assert len(result) <= 40
    assert result[-13:-12] == "-"  # Has digest suffix

    # With max_len=100, should not truncate
    result = _base_slug(image, max_len=100)
    assert result == "ghcr.io_s_org_s_very-long-repository-name_tag_v1.2.3"
    assert len(result) < 100


def test_base_slug_digest_consistency():
    """Test that the same image always produces the same digest."""
    from openhands.agent_server.docker.build import _base_slug

    long_image = (
        "ghcr.io/very-long-organization-name/"
        "very-long-repository-name:very-long-tag-v1.2.3"
    )

    result1 = _base_slug(long_image, max_len=50)
    result2 = _base_slug(long_image, max_len=50)

    # Same input should always produce same output
    assert result1 == result2

    # Different input should produce different digest
    different_image = long_image.replace("v1.2.3", "v1.2.4")
    result3 = _base_slug(different_image, max_len=50)
    assert result1 != result3


def test_base_slug_edge_case_exact_max_len():
    """Test base_slug when slug length exactly equals max_len."""
    from openhands.agent_server.docker.build import _base_slug

    # Create an image that results in exactly 30 characters
    # "python_tag_3.12" is 15 chars, let's use it with max_len=15
    result = _base_slug("python:3.12", max_len=15)
    assert result == "python_tag_3.12"
    assert len(result) == 15
