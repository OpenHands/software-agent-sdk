"""Tests for plugin source path handling."""

from pathlib import Path

import pytest

from openhands.sdk.plugin.source import (
    is_local_path,
    parse_github_url,
    resolve_source_path,
    validate_source_path,
)


class TestParseGitHubURL:
    def test_parse_blob_url(self):
        result = parse_github_url(
            "https://github.com/OpenHands/extensions/blob/main/skills/github"
        )
        assert result is not None
        assert result.owner == "OpenHands"
        assert result.repo == "extensions"
        assert result.branch == "main"
        assert result.path == "skills/github"

    def test_parse_tree_url(self):
        result = parse_github_url(
            "https://github.com/OpenHands/extensions/tree/main/skills/github"
        )
        assert result is not None
        assert result.path == "skills/github"

    def test_returns_none_for_non_github(self):
        assert parse_github_url("./skills/my-skill") is None
        assert parse_github_url("https://gitlab.com/o/r/blob/main/p") is None


class TestIsLocalPath:
    def test_local_paths(self):
        assert is_local_path("./skills/my-skill")
        assert is_local_path("../parent/skill")
        assert is_local_path("/absolute/path")
        assert is_local_path("~/home/path")
        assert is_local_path("file:///path/to/file")

    def test_non_local_paths(self):
        assert not is_local_path("https://github.com/o/r/blob/main/p")
        assert not is_local_path("just-a-name")


class TestValidateSourcePath:
    def test_valid_paths(self):
        assert validate_source_path("./skills/my-skill") == "./skills/my-skill"
        assert validate_source_path("/absolute/path") == "/absolute/path"
        url = "https://github.com/owner/repo/blob/main/path"
        assert validate_source_path(url) == url

    def test_invalid_source_raises(self):
        with pytest.raises(ValueError, match="Invalid source path"):
            validate_source_path("just-a-name")


class TestResolveSourcePath:
    def test_resolve_file_url(self):
        assert resolve_source_path("file:///tmp/skill") == Path("/tmp/skill")

    def test_resolve_absolute_path(self):
        assert resolve_source_path("/absolute/path") == Path("/absolute/path")

    def test_resolve_relative_with_base(self):
        result = resolve_source_path("./skill", base_path=Path("/project"))
        assert result == (Path("/project") / "skill").resolve()

    def test_resolve_home_path(self):
        result = resolve_source_path("~/documents/skill")
        assert result == Path.home() / "documents" / "skill"


class TestSourceEdgeCases:
    def test_parse_github_url_requires_path_after_branch(self):
        assert parse_github_url("https://github.com/o/r/tree/main") is None
        assert parse_github_url("https://github.com/o/r") is None

    def test_parse_github_url_rejects_non_https_and_other_hosts(self):
        assert parse_github_url("http://github.com/o/r/blob/main/p") is None
        assert parse_github_url("https://www.github.com/o/r/blob/main/p") is None

    @pytest.mark.parametrize("source", ["", "   ", "owner/repo", "github:o/r"])
    def test_validate_source_path_rejects_ambiguous_sources(self, source):
        with pytest.raises(ValueError, match="Invalid source path"):
            validate_source_path(source)

    def test_resolve_github_source_returns_none_when_clone_fails(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            "openhands.sdk.plugin.source.try_cached_clone_or_update",
            lambda *args, **kwargs: None,
        )

        result = resolve_source_path(
            "https://github.com/o/r/blob/main/skills/x", cache_dir=tmp_path
        )

        assert result is None

    def test_resolve_github_source_lowercases_cache_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "openhands.sdk.plugin.source.try_cached_clone_or_update",
            lambda url, repo_path, *args, **kwargs: repo_path,
        )

        result = resolve_source_path(
            "https://github.com/Owner/Repo/tree/main/skills/x", cache_dir=tmp_path
        )

        assert result == tmp_path / "github.com" / "owner" / "repo" / "skills" / "x"

    @pytest.mark.xfail(
        strict=True,
        reason="BUG: file:// URLs are sliced instead of parsed, so the "
        "'localhost' authority becomes a relative path component",
    )
    def test_resolve_file_url_with_localhost_authority(self):
        assert resolve_source_path("file://localhost/tmp/skill") == Path("/tmp/skill")

    @pytest.mark.xfail(
        strict=True,
        reason="BUG: file:// URLs are sliced instead of parsed, so "
        "percent-escapes such as %20 are not decoded",
    )
    def test_resolve_file_url_decodes_percent_escapes(self):
        assert resolve_source_path("file:///tmp/my%20skill") == Path("/tmp/my skill")
