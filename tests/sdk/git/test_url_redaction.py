"""Tests for URL credential redaction utilities."""

import subprocess
from unittest.mock import patch

import pytest

from openhands.sdk.git.exceptions import GitCommandError
from openhands.sdk.git.utils import (
    redact_url_credentials,
    run_git_command,
)  # re-exported for compat
from openhands.sdk.plugin.types import PluginSource, ResolvedPluginSource
from openhands.sdk.utils.redact import (
    redact_url_credentials as redact_url_credentials_central,
)


class TestRedactUrlCredentials:
    """Tests for redact_url_credentials function."""

    def test_https_with_oauth2_token(self):
        """Should redact oauth2 tokens in HTTPS URLs."""
        url = "https://oauth2:SECRET_TOKEN@gitlab.com/org/repo.git"
        result = redact_url_credentials(url)
        assert result == "https://****@gitlab.com/org/repo.git"
        assert "SECRET_TOKEN" not in result

    def test_https_with_username_password(self):
        """Should redact username:password in HTTPS URLs."""
        url = "https://user:password123@github.com/owner/repo.git"
        result = redact_url_credentials(url)
        assert result == "https://****@github.com/owner/repo.git"
        assert "user" not in result
        assert "password123" not in result

    def test_https_with_x_token_auth(self):
        """Should redact x-token-auth credentials (Bitbucket style)."""
        url = "https://x-token-auth:MY_TOKEN@bitbucket.org/team/repo.git"
        result = redact_url_credentials(url)
        assert result == "https://****@bitbucket.org/team/repo.git"
        assert "MY_TOKEN" not in result

    def test_https_with_just_token(self):
        """Should redact token-only auth (GitHub PAT style)."""
        url = "https://ghp_xxxxxxxxxxxx@github.com/owner/repo.git"
        result = redact_url_credentials(url)
        assert result == "https://****@github.com/owner/repo.git"
        assert "ghp_xxxxxxxxxxxx" not in result

    def test_https_without_credentials(self):
        """Should not modify URLs without credentials."""
        url = "https://github.com/owner/repo.git"
        result = redact_url_credentials(url)
        assert result == url

    def test_http_with_credentials(self):
        """Should redact credentials in HTTP URLs."""
        url = "http://user:pass@internal-git.company.com/repo.git"
        result = redact_url_credentials(url)
        assert result == "http://****@internal-git.company.com/repo.git"
        assert "user" not in result
        assert "pass" not in result

    def test_ssh_url_unchanged(self):
        """Should not modify SSH URLs (no embedded credentials)."""
        url = "git@github.com:owner/repo.git"
        result = redact_url_credentials(url)
        assert result == url

    def test_ssh_gitlab_url_unchanged(self):
        """Should not modify GitLab SSH URLs."""
        url = "git@gitlab.com:org/project.git"
        result = redact_url_credentials(url)
        assert result == url

    def test_local_path_unchanged(self):
        """Should not modify local paths."""
        path = "/path/to/local/repo"
        result = redact_url_credentials(path)
        assert result == path

    def test_github_shorthand_unchanged(self):
        """Should not modify GitHub shorthand format (no credentials)."""
        source = "github:owner/repo"
        result = redact_url_credentials(source)
        assert result == source

    def test_preserves_port_in_url(self):
        """Should preserve port numbers in URLs while redacting credentials."""
        url = "https://user:pass@git.company.com:8443/repo.git"
        result = redact_url_credentials(url)
        assert result == "https://****@git.company.com:8443/repo.git"
        assert "8443" in result
        assert "user" not in result

    def test_preserves_path_with_subdirectories(self):
        """Should preserve full path with subdirectories."""
        url = "https://token@github.com/org/repo/tree/main/subdir"
        result = redact_url_credentials(url)
        assert result == "https://****@github.com/org/repo/tree/main/subdir"
        assert "/org/repo/tree/main/subdir" in result

    def test_empty_string(self):
        """Should handle empty string."""
        result = redact_url_credentials("")
        assert result == ""

    def test_special_characters_in_token(self):
        """Should handle special characters in tokens."""
        # URL-encoded special characters in credentials
        url = "https://user%40domain:p%40ss%3Aword@github.com/repo.git"
        result = redact_url_credentials(url)
        assert result == "https://****@github.com/repo.git"
        assert "user%40domain" not in result


class TestResolvedPluginSourceCredentialRedaction:
    """Tests for credential redaction in ResolvedPluginSource."""

    def test_from_plugin_source_redacts_credentials(self):
        """Should redact credentials when creating from PluginSource."""
        plugin_source = PluginSource(
            source="https://oauth2:SECRET_TOKEN@gitlab.com/org/repo.git",
            ref="main",
        )
        resolved = ResolvedPluginSource.from_plugin_source(
            plugin_source, resolved_ref="abc123def456"
        )
        assert resolved.source == "https://****@gitlab.com/org/repo.git"
        assert "SECRET_TOKEN" not in resolved.source
        assert resolved.resolved_ref == "abc123def456"
        assert resolved.original_ref == "main"

    def test_from_plugin_source_preserves_url_without_credentials(self):
        """Should not modify URLs without credentials."""
        plugin_source = PluginSource(
            source="https://github.com/owner/repo.git",
            ref="v1.0.0",
        )
        resolved = ResolvedPluginSource.from_plugin_source(
            plugin_source, resolved_ref="abc123def456"
        )
        assert resolved.source == "https://github.com/owner/repo.git"
        assert resolved.resolved_ref == "abc123def456"

    def test_from_plugin_source_preserves_local_path(self):
        """Should not modify local paths."""
        plugin_source = PluginSource(source="/path/to/local/plugin")
        resolved = ResolvedPluginSource.from_plugin_source(
            plugin_source, resolved_ref=None
        )
        assert resolved.source == "/path/to/local/plugin"
        assert resolved.resolved_ref is None

    def test_from_plugin_source_preserves_repo_path(self):
        """Should preserve repo_path when redacting credentials."""
        plugin_source = PluginSource(
            source="https://token@github.com/org/monorepo.git",
            ref="main",
            repo_path="plugins/my-plugin",
        )
        resolved = ResolvedPluginSource.from_plugin_source(
            plugin_source, resolved_ref="abc123"
        )
        assert resolved.source == "https://****@github.com/org/monorepo.git"
        assert resolved.repo_path == "plugins/my-plugin"

    def test_to_plugin_source_uses_redacted_url(self):
        """When converting back to PluginSource, should use the redacted URL."""
        # Simulate a ResolvedPluginSource loaded from persistence
        resolved = ResolvedPluginSource(
            source="https://****@gitlab.com/org/repo.git",  # Already redacted
            resolved_ref="abc123def456",
            repo_path=None,
            original_ref="main",
        )
        plugin_source = resolved.to_plugin_source()
        assert plugin_source.source == "https://****@gitlab.com/org/repo.git"
        assert plugin_source.ref == "abc123def456"  # Uses resolved ref, not original

    def test_serialization_does_not_expose_credentials(self):
        """Ensure JSON serialization doesn't expose credentials."""
        plugin_source = PluginSource(
            source="https://oauth2:SUPER_SECRET@gitlab.com/org/repo.git",
            ref="main",
        )
        resolved = ResolvedPluginSource.from_plugin_source(
            plugin_source, resolved_ref="abc123"
        )
        json_str = resolved.model_dump_json()
        assert "SUPER_SECRET" not in json_str
        assert "****" in json_str


class TestRedactUrlCredentialsCentralModule:
    """Verify redact_url_credentials is accessible from the central redact module."""

    def test_same_behaviour_as_git_utils(self):
        url = "https://oauth2:SECRET@gitlab.com/org/repo.git"
        assert redact_url_credentials(url) == redact_url_credentials_central(url)

    def test_importable_from_sdk_utils_redact(self):
        assert (
            redact_url_credentials_central("https://t@host/r") == "https://****@host/r"
        )


CREDENTIAL_URL = "https://oauth2:SUPERSECRET@github.com/o/r.git"
REDACTED_URL = "https://****@github.com/o/r.git"


class TestRunGitCommandCredentialRedaction:
    """GitCommandError.command must never carry raw credentials across all raise sites."""

    def _args(self):
        return ["git", "clone", CREDENTIAL_URL, "/tmp/x"]

    def test_nonzero_returncode_redacts_command(self):
        completed = subprocess.CompletedProcess(
            args=self._args(), returncode=128, stdout="", stderr="fatal: repo not found"
        )
        with patch("subprocess.run", return_value=completed):
            with pytest.raises(GitCommandError) as exc_info:
                run_git_command(self._args())
        assert CREDENTIAL_URL not in exc_info.value.command
        assert REDACTED_URL in exc_info.value.command

    def test_timeout_expired_redacts_command(self):
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=self._args(), timeout=30),
        ):
            with pytest.raises(GitCommandError) as exc_info:
                run_git_command(self._args())
        assert CREDENTIAL_URL not in exc_info.value.command
        assert REDACTED_URL in exc_info.value.command

    def test_file_not_found_redacts_command(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(GitCommandError) as exc_info:
                run_git_command(["git-not-on-path", "clone", CREDENTIAL_URL, "/tmp/x"])
        assert CREDENTIAL_URL not in exc_info.value.command
        assert REDACTED_URL in exc_info.value.command
