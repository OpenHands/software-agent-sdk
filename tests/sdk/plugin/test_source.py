"""Tests for plugin source path handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from openhands.sdk.plugin.source import (
    SourcePath,
    get_cache_path_for_github_repo,
    is_github_url,
    is_local_path,
    parse_github_url,
    validate_source_path,
)


class TestParseGitHubURL:
    """Tests for parse_github_url function."""

    def test_parse_blob_url(self):
        """Test parsing a GitHub blob URL."""
        url = "https://github.com/OpenHands/extensions/blob/main/skills/github/SKILL.md"
        result = parse_github_url(url)

        assert result is not None
        assert result.owner == "OpenHands"
        assert result.repo == "extensions"
        assert result.branch == "main"
        assert result.path == "skills/github/SKILL.md"

    def test_parse_tree_url(self):
        """Test parsing a GitHub tree URL (directory)."""
        url = "https://github.com/OpenHands/extensions/tree/main/skills/github"
        result = parse_github_url(url)

        assert result is not None
        assert result.owner == "OpenHands"
        assert result.repo == "extensions"
        assert result.branch == "main"
        assert result.path == "skills/github"

    def test_parse_url_with_deep_path(self):
        """Test parsing URL with deep nested path."""
        url = "https://github.com/org/repo/blob/v1.0.0/a/b/c/d/e/file.txt"
        result = parse_github_url(url)

        assert result is not None
        assert result.branch == "v1.0.0"
        assert result.path == "a/b/c/d/e/file.txt"

    def test_parse_local_path_returns_none(self):
        """Test that local paths return None."""
        assert parse_github_url("./skills/my-skill") is None
        assert parse_github_url("/absolute/path") is None
        assert parse_github_url("relative/path") is None

    def test_parse_invalid_github_url_returns_none(self):
        """Test that invalid GitHub URLs return None."""
        # Missing blob/tree
        assert parse_github_url("https://github.com/owner/repo/main/path") is None
        # Not GitHub
        assert parse_github_url("https://gitlab.com/owner/repo/blob/main/path") is None


class TestIsLocalPath:
    """Tests for is_local_path function."""

    def test_relative_paths(self):
        """Test detection of relative paths."""
        assert is_local_path("./skills/my-skill") is True
        assert is_local_path("../parent/skill") is True

    def test_absolute_paths(self):
        """Test detection of absolute paths."""
        assert is_local_path("/absolute/path") is True
        assert is_local_path("~/home/path") is True

    def test_file_urls(self):
        """Test detection of file:// URLs."""
        assert is_local_path("file:///path/to/file") is True

    def test_non_local_paths(self):
        """Test that non-local paths return False."""
        assert is_local_path("https://github.com/owner/repo/blob/main/path") is False
        assert is_local_path("just-a-name") is False


class TestIsGitHubURL:
    """Tests for is_github_url function."""

    def test_valid_github_urls(self):
        """Test detection of valid GitHub URLs."""
        assert is_github_url("https://github.com/owner/repo/blob/main/path") is True
        assert is_github_url("https://github.com/owner/repo/tree/main/path") is True

    def test_invalid_urls(self):
        """Test that non-GitHub URLs return False."""
        assert is_github_url("./local/path") is False
        assert is_github_url("/absolute/path") is False
        assert is_github_url("https://gitlab.com/owner/repo/blob/main/path") is False


class TestValidateSourcePath:
    """Tests for validate_source_path function."""

    def test_valid_local_paths(self):
        """Test validation of valid local paths."""
        assert validate_source_path("./skills/my-skill") == "./skills/my-skill"
        assert validate_source_path("../parent/skill") == "../parent/skill"
        assert validate_source_path("/absolute/path") == "/absolute/path"
        assert validate_source_path("~/home/path") == "~/home/path"
        assert validate_source_path("file:///path/to/file") == "file:///path/to/file"

    def test_valid_github_urls(self):
        """Test validation of valid GitHub URLs."""
        url = "https://github.com/owner/repo/blob/main/path"
        assert validate_source_path(url) == url

    def test_invalid_source_raises(self):
        """Test that invalid sources raise ValueError."""
        with pytest.raises(ValueError, match="Invalid source path"):
            validate_source_path("just-a-name")

        with pytest.raises(ValueError, match="Invalid source path"):
            validate_source_path("https://gitlab.com/owner/repo/blob/main/path")


class TestSourcePath:
    """Tests for SourcePath custom type."""

    def test_create_local_path(self):
        """Test creating a SourcePath from a local path."""
        sp = SourcePath("./skills/my-skill")
        assert sp == "./skills/my-skill"
        assert sp.is_local is True
        assert sp.is_github is False

    def test_create_github_url(self):
        """Test creating a SourcePath from a GitHub URL."""
        url = "https://github.com/owner/repo/blob/main/skills/test"
        sp = SourcePath(url)
        assert sp == url
        assert sp.is_local is False
        assert sp.is_github is True
        assert sp.github_components is not None
        assert sp.github_components.owner == "owner"

    def test_invalid_source_raises(self):
        """Test that invalid source raises ValueError."""
        with pytest.raises(ValueError):
            SourcePath("invalid-source")


class TestGetCachePathForGitHubRepo:
    """Tests for get_cache_path_for_github_repo function."""

    def test_default_cache_dir(self):
        """Test cache path with default directory."""
        path = get_cache_path_for_github_repo("OpenHands", "extensions")
        assert (
            path
            == Path.home()
            / ".openhands"
            / "cache"
            / "git"
            / "github.com"
            / "openhands"
            / "extensions"
        )

    def test_custom_cache_dir(self):
        """Test cache path with custom directory."""
        custom_dir = Path("/custom/cache")
        path = get_cache_path_for_github_repo("Owner", "Repo", cache_dir=custom_dir)
        assert path == custom_dir / "github.com" / "owner" / "repo"

    def test_lowercase_conversion(self):
        """Test that owner and repo are lowercased."""
        path = get_cache_path_for_github_repo("MixedCase", "UPPERCASE")
        assert "mixedcase" in str(path)
        assert "uppercase" in str(path)
        assert "MixedCase" not in str(path)
        assert "UPPERCASE" not in str(path)
