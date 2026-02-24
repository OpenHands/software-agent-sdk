"""Tests for credential redaction in git utilities."""

from openhands.sdk.git.utils import redact_url_credentials
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

    def test_empty_string(self) -> None:
        """Should handle empty string gracefully."""
        result = redact_url_credentials("")
        assert result == ""

    def test_complex_url_with_port(self) -> None:
        """Should handle URLs with port numbers."""
        url = "https://user:pass@gitlab.example.com:8443/org/repo.git"
        result = redact_url_credentials(url)
        assert result == "https://****@gitlab.example.com:8443/org/repo.git"
        assert "pass" not in result

    def test_url_with_special_chars_in_password(self) -> None:
        """Should handle special characters in credentials."""
        # Password with special chars like @, :, etc.
        url = "https://user:p%40ss%3Aword@github.com/owner/repo.git"
        result = redact_url_credentials(url)
        assert result == "https://****@github.com/owner/repo.git"
        assert "p%40ss%3Aword" not in result


class TestPublicAPIExport:
    """Tests for public API export of redact_url_credentials."""

    def test_import_from_sdk(self) -> None:
        """Should be importable from openhands.sdk."""
        from openhands.sdk import redact_url_credentials as sdk_redact

        # Verify it's the same function
        assert sdk_redact is redact_url_credentials

    def test_function_works_via_sdk_import(self) -> None:
        """Should work correctly when imported from SDK."""
        from openhands.sdk import redact_url_credentials as sdk_redact

        url = "https://token@github.com/owner/repo.git"
        result = sdk_redact(url)
        assert result == "https://****@github.com/owner/repo.git"


class TestResolvedPluginSourceCredentialRedaction:
    """Tests for credential redaction in ResolvedPluginSource persistence."""

    def test_from_plugin_source_redacts_credentials(self) -> None:
        """Should redact credentials when creating ResolvedPluginSource."""
        plugin_source = PluginSource(
            source="https://oauth2:SECRET_TOKEN@gitlab.com/org/private-repo.git",
            ref="main",
            repo_path="plugins/my-plugin",
        )

        resolved = ResolvedPluginSource.from_plugin_source(
            plugin_source, resolved_ref="abc123def456"
        )

        # Source should be redacted
        assert resolved.source == "https://****@gitlab.com/org/private-repo.git"
        assert "SECRET_TOKEN" not in resolved.source
        assert "oauth2" not in resolved.source

        # Other fields should be preserved
        assert resolved.resolved_ref == "abc123def456"
        assert resolved.repo_path == "plugins/my-plugin"
        assert resolved.original_ref == "main"

    def test_from_plugin_source_preserves_url_without_credentials(self) -> None:
        """Should not modify URLs that don't have credentials."""
        plugin_source = PluginSource(
            source="https://github.com/owner/repo.git",
            ref="v1.0.0",
        )

        resolved = ResolvedPluginSource.from_plugin_source(
            plugin_source, resolved_ref="def456"
        )

        assert resolved.source == "https://github.com/owner/repo.git"
        assert resolved.resolved_ref == "def456"

    def test_from_plugin_source_handles_local_paths(self) -> None:
        """Should not modify local paths."""
        plugin_source = PluginSource(source="/local/path/to/plugin")

        resolved = ResolvedPluginSource.from_plugin_source(
            plugin_source, resolved_ref=None
        )

        assert resolved.source == "/local/path/to/plugin"
        assert resolved.resolved_ref is None

    def test_from_plugin_source_handles_github_shorthand(self) -> None:
        """Should not modify github: shorthand syntax."""
        plugin_source = PluginSource(source="github:owner/repo", ref="main")

        resolved = ResolvedPluginSource.from_plugin_source(
            plugin_source, resolved_ref="abc123"
        )

        # github: shorthand doesn't contain credentials
        assert resolved.source == "github:owner/repo"

    def test_to_plugin_source_uses_redacted_url(self) -> None:
        """Converted PluginSource should use the redacted URL."""
        plugin_source = PluginSource(
            source="https://token@github.com/owner/repo.git",
            ref="main",
        )

        resolved = ResolvedPluginSource.from_plugin_source(
            plugin_source, resolved_ref="abc123"
        )

        converted = resolved.to_plugin_source()

        # Source should still be redacted in converted object
        assert converted.source == "https://****@github.com/owner/repo.git"
        # Should use resolved_ref, not original_ref
        assert converted.ref == "abc123"
