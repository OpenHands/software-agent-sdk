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


def test_truncate_ident_cases():
    """Exercise _truncate_ident priority rules."""
    from openhands.agent_server.docker.build import _truncate_ident

    assert _truncate_ident("repo", "v1", 20) == "repo_tag_v1"
    assert _truncate_ident("averylongrepo", "tag", 10) == "av_tag_tag"
    assert _truncate_ident("repo", "averylongtag", 8) == "_tag_ave"
    assert _truncate_ident("averylongrepo", "", 5) == "avery"


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

    # Check the exact truncated output for determinism
    assert result == "very-lon_tag_very-long-tag-v1.2.3-alpha.1+build.123-cdb8db90d8c5"


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

    # Check the exact truncated output for determinism
    assert result == "very-long-repository-name-that-exceeds-max-length-2a772685291d"


def test_base_slug_preserves_latest_tag_suffix():
    """Ensure tag_latest suffix is not mangled when truncating long slugs."""
    from openhands.agent_server.docker.build import _base_slug

    image = (
        "docker.io/swebench/sweb.eval.x86_64.astropy_1776_astropy-8872:"
        "tag_latest-0a797356ebce"
    )

    result = _base_slug(image, max_len=64)

    assert len(result) <= 64
    assert result == "sweb.eval.x86_64.astropy_17_tag_latest-0a797356ebce-e023ce15bc3b"


def test_base_slug_preserves_tag_with_registry_port():
    """Handle registries with ports without losing the tag segment."""
    from openhands.agent_server.docker.build import _base_slug

    image = (
        "localhost:5001/swebench/sweb.eval.x86_64.astropy_1776_astropy-8872:"
        "tag_latest-0a797356ebce"
    )

    result = _base_slug(image, max_len=64)

    assert len(result) <= 64
    assert result == "sweb.eval.x86_64.astropy_17_tag_latest-0a797356ebce-0138a908f35e"


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


def test_versioned_tags_single_custom_tag():
    """Test versioned_tags with a single custom tag."""
    from openhands.agent_server.docker.build import BuildOptions

    opts = BuildOptions(
        custom_tags="python",
        sdk_version="1.2.0",
        include_versioned_tag=True,
    )

    versioned_tags = opts.versioned_tags
    assert versioned_tags == ["1.2.0-python"]


def test_versioned_tags_multiple_custom_tags():
    """Test versioned_tags with multiple custom tags."""
    from openhands.agent_server.docker.build import BuildOptions

    opts = BuildOptions(
        custom_tags="python,java,golang",
        sdk_version="1.2.0",
        include_versioned_tag=True,
    )

    versioned_tags = opts.versioned_tags
    assert versioned_tags == ["1.2.0-python", "1.2.0-java", "1.2.0-golang"]


def test_versioned_tags_no_custom_tags():
    """Test versioned_tags when no custom tags are provided."""
    from openhands.agent_server.docker.build import BuildOptions

    opts = BuildOptions(
        custom_tags="",
        sdk_version="1.2.0",
        include_versioned_tag=True,
    )

    versioned_tags = opts.versioned_tags
    assert versioned_tags == []


def test_all_tags_includes_versioned_tags():
    """Test that all_tags includes versioned tags when enabled."""
    from openhands.agent_server.docker.build import BuildOptions

    opts = BuildOptions(
        custom_tags="python,java",
        sdk_version="1.2.0",
        git_sha="abc1234567890",
        include_versioned_tag=True,
        include_base_tag=False,  # Simplify by excluding base tag
    )

    all_tags = opts.all_tags

    # Should include commit-based tags
    assert "ghcr.io/openhands/agent-server:abc1234-python" in all_tags
    assert "ghcr.io/openhands/agent-server:abc1234-java" in all_tags

    # Should include versioned tags
    assert "ghcr.io/openhands/agent-server:1.2.0-python" in all_tags
    assert "ghcr.io/openhands/agent-server:1.2.0-java" in all_tags


def test_all_tags_excludes_versioned_tags_when_disabled():
    """Test that all_tags excludes versioned tags when disabled."""
    from openhands.agent_server.docker.build import BuildOptions

    opts = BuildOptions(
        custom_tags="python",
        sdk_version="1.2.0",
        git_sha="abc1234567890",
        include_versioned_tag=False,
        include_base_tag=False,
    )

    all_tags = opts.all_tags

    # Should include commit-based tag
    assert "ghcr.io/openhands/agent-server:abc1234-python" in all_tags

    # Should NOT include versioned tags
    assert "ghcr.io/openhands/agent-server:1.2.0-python" not in all_tags


def test_all_tags_with_arch_suffix():
    """Test that versioned tags include architecture suffix."""
    from openhands.agent_server.docker.build import BuildOptions

    opts = BuildOptions(
        custom_tags="python",
        sdk_version="1.2.0",
        git_sha="abc1234567890",
        arch="amd64",
        include_versioned_tag=True,
        include_base_tag=False,
    )

    all_tags = opts.all_tags

    # Should include versioned tag with arch suffix
    assert "ghcr.io/openhands/agent-server:1.2.0-python-amd64" in all_tags
    assert "ghcr.io/openhands/agent-server:abc1234-python-amd64" in all_tags


def test_all_tags_with_target_suffix():
    """Test that versioned tags include target suffix for non-binary targets."""
    from openhands.agent_server.docker.build import BuildOptions

    opts = BuildOptions(
        custom_tags="python",
        sdk_version="1.2.0",
        git_sha="abc1234567890",
        target="source",
        include_versioned_tag=True,
        include_base_tag=False,
    )

    all_tags = opts.all_tags

    # Should include versioned tag with target suffix
    assert "ghcr.io/openhands/agent-server:1.2.0-python-source" in all_tags
    assert "ghcr.io/openhands/agent-server:abc1234-python-source" in all_tags


def test_versioned_tags_format_without_v_prefix():
    """Test that versioned tags don't include 'v' prefix."""
    from openhands.agent_server.docker.build import BuildOptions

    opts = BuildOptions(
        custom_tags="python",
        sdk_version="1.2.0",
        include_versioned_tag=True,
    )

    versioned_tags = opts.versioned_tags

    # Should be "1.2.0-python", not "v1.2.0-python"
    assert versioned_tags == ["1.2.0-python"]
    assert not any(tag.startswith("v") for tag in versioned_tags)
