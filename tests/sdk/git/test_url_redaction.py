"""Tests for URL credential redaction utilities."""

import logging
import subprocess
import sys
from unittest.mock import patch

import pytest

from openhands.sdk.git.exceptions import GitCommandError
from openhands.sdk.git.utils import (
    get_git_repository_metadata,
    redact_url_credentials,
    run_git_command,
    run_git_subprocess,
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

    def test_preserve_placeholders_keeps_var_reference(self):
        """A ${VAR} userinfo is not a secret and must survive when asked."""
        url = "https://x-token-auth:${MY_TOKEN}@host/repo.git"
        assert redact_url_credentials(url, preserve_placeholders=True) == url
        # Without the flag it is masked like any other userinfo.
        assert redact_url_credentials(url) == "https://****@host/repo.git"

    def test_preserve_placeholders_still_masks_inline_credentials(self):
        """preserve_placeholders only spares ${VAR}; real inline creds are masked."""
        url = "https://oauth2:SECRET@github.com/org/repo.git"
        result = redact_url_credentials(url, preserve_placeholders=True)
        assert result == "https://****@github.com/org/repo.git"
        assert "SECRET" not in result


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


class TestPluginSourceCredentialRedaction:
    """PluginSource.source masks inline credentials at serialization time while
    keeping the raw value in memory for fetch/clone and preserving ${VAR} refs."""

    CRED = "https://oauth2:SUPER_SECRET@gitlab.com/org/repo.git"
    REDACTED = "https://****@gitlab.com/org/repo.git"
    PLACEHOLDER = "https://x-token-auth:${MY_TOKEN}@host/repo.git"

    def test_default_dump_masks_only_the_credential(self):
        ps = PluginSource(source=self.CRED)
        # URL shape is kept (not a full ********** mask).
        assert ps.model_dump()["source"] == self.REDACTED
        assert "SUPER_SECRET" not in ps.model_dump_json()

    def test_in_memory_value_is_raw_for_fetch(self):
        # The attribute keeps the real URL so the plugin can still be cloned.
        assert PluginSource(source=self.CRED).source == self.CRED

    def test_placeholder_survives_dump(self):
        # ${VAR} is not a secret; it is expanded from the secret registry at
        # fetch time (incl. server-side), so it must survive every dump.
        ps = PluginSource(source=self.PLACEHOLDER)
        assert ps.model_dump()["source"] == self.PLACEHOLDER
        assert ps.model_dump_json().count("${MY_TOKEN}") == 1

    @pytest.mark.parametrize(
        "source",
        [
            "github:owner/repo",
            "/path/to/local/plugin",
            "git@github.com:owner/repo.git",
            "https://github.com/owner/repo.git",
        ],
    )
    def test_non_credential_sources_unchanged(self, source):
        assert PluginSource(source=source).model_dump()["source"] == source

    def test_redacted_dump_round_trips(self):
        # Reloading a redacted dump does not raise; the source stays redacted
        # (resume of an inline-credentialed plugin relies on the resolved SHA).
        dumped = PluginSource(source=self.CRED).model_dump()
        assert PluginSource.model_validate(dumped).source == self.REDACTED


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
    """Credentials must not leak into GitCommandError.command on any error path."""

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

    def test_stderr_credentials_redacted_on_exception(self):
        """Credentials echoed in stderr must not leak onto GitCommandError.stderr."""
        leaky_stderr = f"fatal: Authentication failed for '{CREDENTIAL_URL}/'"
        completed = subprocess.CompletedProcess(
            args=self._args(), returncode=128, stdout="", stderr=leaky_stderr
        )
        with patch("subprocess.run", return_value=completed):
            with pytest.raises(GitCommandError) as exc_info:
                run_git_command(self._args())
        assert "SUPERSECRET" not in exc_info.value.stderr
        assert REDACTED_URL in exc_info.value.stderr

    def test_stderr_credentials_redacted_in_log(self, caplog):
        """Credentials echoed in stderr must not leak into the error log line."""
        leaky_stderr = f"fatal: Authentication failed for '{CREDENTIAL_URL}/'"
        completed = subprocess.CompletedProcess(
            args=self._args(), returncode=128, stdout="", stderr=leaky_stderr
        )
        with patch("subprocess.run", return_value=completed):
            with caplog.at_level(logging.ERROR):
                with pytest.raises(GitCommandError):
                    run_git_command(self._args())
        assert "SUPERSECRET" not in caplog.text
        assert REDACTED_URL in caplog.text


def test_run_git_subprocess_replaces_undecodable_stdout_bytes():
    """Invalid-encoding stdout must not raise UnicodeDecodeError (AGE-1871)."""
    result = run_git_subprocess(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff\\xfe')"],
        cwd=None,
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout == "��"


def test_get_git_repository_metadata():
    responses = [
        subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="HTTPS://user:secret@github.com/org/repo.git\n",
            stderr="",
        ),
        subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="abc123\nfeature-x\n",
            stderr="",
        ),
    ]
    with patch("openhands.sdk.git.utils.run_git_subprocess", side_effect=responses):
        metadata = get_git_repository_metadata("/repo")

    assert metadata == {
        "repo_remote": "HTTPS://****@github.com/org/repo.git",
        "head_commit": "abc123",
        "branch": "feature-x",
    }
