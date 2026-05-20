"""Tests for credential redaction in git utilities."""

import pytest

from openhands.sdk.git.utils import _redact_git_args, redact_url_credentials
from openhands.sdk.plugin.types import PluginSource, ResolvedPluginSource


class TestRedactUrlCredentials:
    """Tests for redact_url_credentials function."""

    def test_https_url_with_user_password(self) -> None:
        """Should redact user:password credentials in HTTPS URLs."""
        url = "https://user:password@github.com/owner/repo.git"
        result = redact_url_credentials(url)
        assert result == "https://****@github.com/owner/repo.git"
        assert "password" not in result

    def test_https_url_with_oauth2_token(self) -> None:
        """Should redact oauth2:token credentials in HTTPS URLs."""
        url = "https://oauth2:SECRET_TOKEN@gitlab.com/org/repo.git"
        result = redact_url_credentials(url)
        assert result == "https://****@gitlab.com/org/repo.git"
        assert "SECRET_TOKEN" not in result
        assert "oauth2" not in result

    def test_https_url_with_token_only(self) -> None:
        """Should redact token-only credentials in HTTPS URLs."""
        url = "https://ghp_supersecrettoken@github.com/owner/repo.git"
        result = redact_url_credentials(url)
        assert result == "https://****@github.com/owner/repo.git"
        assert "ghp_supersecrettoken" not in result

    def test_http_url_with_credentials(self) -> None:
        """Should redact credentials in HTTP URLs."""
        url = "http://user:pass@example.com/repo.git"
        result = redact_url_credentials(url)
        assert result == "http://****@example.com/repo.git"
        assert "pass" not in result

    def test_https_url_without_credentials(self) -> None:
        """Should not modify URLs without credentials."""
        url = "https://github.com/owner/repo.git"
        result = redact_url_credentials(url)
        assert result == url

    def test_ssh_url_not_modified(self) -> None:
        """Should not modify SSH-style git URLs (they don't use embedded creds)."""
        url = "git@github.com:owner/repo.git"
        result = redact_url_credentials(url)
        assert result == url

    def test_git_protocol_url(self) -> None:
        """Should not modify git:// protocol URLs."""
        url = "git://github.com/owner/repo.git"
        result = redact_url_credentials(url)
        assert result == url

    def test_local_path_not_modified(self) -> None:
        """Should not modify local paths."""
        path = "/local/path/to/repo"
        result = redact_url_credentials(path)
        assert result == path

    def test_github_shorthand_not_modified(self) -> None:
        """Should not modify github: shorthand syntax."""
        source = "github:owner/repo"
        result = redact_url_credentials(source)
        assert result == source

    def test_url_with_port(self) -> None:
        """Should handle URLs with ports correctly."""
        url = "https://user:pass@gitlab.example.com:8443/repo.git"
        result = redact_url_credentials(url)
        assert result == "https://****@gitlab.example.com:8443/repo.git"
        assert "pass" not in result

    def test_url_with_special_characters_in_password(self) -> None:
        """Should handle special characters in credentials."""
        url = "https://user:p%40ss!word@github.com/owner/repo.git"
        result = redact_url_credentials(url)
        assert result == "https://****@github.com/owner/repo.git"
        assert "p%40ss!word" not in result

    def test_empty_string(self) -> None:
        """Should handle empty string gracefully."""
        result = redact_url_credentials("")
        assert result == ""


class TestRedactGitArgs:
    """Tests for _redact_git_args function."""

    def test_redacts_url_in_clone_command(self) -> None:
        """Should redact credentials in git clone arguments."""
        args = ["git", "clone", "https://oauth2:token@gitlab.com/repo.git", "/tmp/dest"]
        result = _redact_git_args(args)
        assert result == ["git", "clone", "https://****@gitlab.com/repo.git", "/tmp/dest"]
        assert "token" not in " ".join(result)

    def test_preserves_non_url_args(self) -> None:
        """Should not modify non-URL arguments."""
        args = ["git", "status", "--porcelain"]
        result = _redact_git_args(args)
        assert result == args

    def test_mixed_args(self) -> None:
        """Should handle mix of URL and non-URL arguments."""
        args = ["git", "remote", "add", "origin", "https://user:pass@github.com/repo.git"]
        result = _redact_git_args(args)
        assert "pass" not in " ".join(result)
        assert "****@github.com" in result[-1]


class TestResolvedPluginSourceRedaction:
    """Tests for credential redaction in ResolvedPluginSource."""

    def test_from_plugin_source_redacts_credentials(self) -> None:
        """Should redact credentials when creating ResolvedPluginSource."""
        plugin_source = PluginSource(
            source="https://oauth2:SECRET_TOKEN@gitlab.com/org/private-plugin",
            ref="main",
        )
        resolved = ResolvedPluginSource.from_plugin_source(plugin_source, "abc1234")

        assert "SECRET_TOKEN" not in resolved.source
        assert "oauth2" not in resolved.source
        assert "****@gitlab.com" in resolved.source
        assert resolved.resolved_ref == "abc1234"

    def test_from_plugin_source_preserves_non_credentialed_url(self) -> None:
        """Should preserve URLs without credentials."""
        plugin_source = PluginSource(
            source="https://github.com/owner/public-plugin",
            ref="v1.0.0",
        )
        resolved = ResolvedPluginSource.from_plugin_source(plugin_source, "def5678")

        assert resolved.source == "https://github.com/owner/public-plugin"
        assert resolved.resolved_ref == "def5678"

    def test_from_plugin_source_preserves_local_path(self) -> None:
        """Should preserve local paths unchanged."""
        plugin_source = PluginSource(source="/local/path/to/plugin")
        resolved = ResolvedPluginSource.from_plugin_source(plugin_source, None)

        assert resolved.source == "/local/path/to/plugin"
        assert resolved.resolved_ref is None

    def test_from_plugin_source_preserves_github_shorthand(self) -> None:
        """Should preserve github: shorthand syntax."""
        plugin_source = PluginSource(source="github:owner/repo", ref="main")
        resolved = ResolvedPluginSource.from_plugin_source(plugin_source, "ghi9012")

        assert resolved.source == "github:owner/repo"


class TestPublicApiExport:
    """Tests that redact_url_credentials is exported from public API."""

    def test_importable_from_sdk(self) -> None:
        """Should be importable from openhands.sdk."""
        from openhands.sdk import redact_url_credentials as public_redact

        # Verify it works
        result = public_redact("https://user:pass@host.com/repo")
        assert "pass" not in result
        assert "****" in result
