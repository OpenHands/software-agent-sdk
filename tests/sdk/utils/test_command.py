from collections import OrderedDict
from unittest.mock import patch

import pytest

from openhands.sdk.utils.command import execute_command, sanitized_env


def test_sanitized_env_returns_copy():
    """Returns a dict copy, not the original."""
    env = {"FOO": "bar"}
    result = sanitized_env(env)
    assert result == {"FOO": "bar"}
    assert result is not env


def test_sanitized_env_defaults_to_os_environ(monkeypatch):
    """When env is None, returns a dict based on os.environ."""
    monkeypatch.setenv("TEST_SANITIZED_ENV_VAR", "test_value")
    result = sanitized_env(None)
    assert result["TEST_SANITIZED_ENV_VAR"] == "test_value"


def test_sanitized_env_accepts_mapping_types():
    """Accepts any Mapping type, not just dict."""
    env: OrderedDict[str, str] = OrderedDict([("KEY", "value")])
    assert isinstance(sanitized_env(env), dict)


@pytest.mark.parametrize(
    ("env", "expected_ld_path"),
    [
        # ORIG present and non-empty: restore original value
        (
            {"LD_LIBRARY_PATH": "/pyinstaller", "LD_LIBRARY_PATH_ORIG": "/original"},
            "/original",
        ),
        # ORIG absent: leave unchanged
        ({"LD_LIBRARY_PATH": "/some/path"}, "/some/path"),
    ],
)
def test_sanitized_env_ld_library_path(env: dict[str, str], expected_ld_path: str):
    """LD_LIBRARY_PATH is restored from ORIG or left unchanged."""
    assert sanitized_env(env)["LD_LIBRARY_PATH"] == expected_ld_path


def test_sanitized_env_removes_ld_library_path_when_orig_empty():
    """When LD_LIBRARY_PATH_ORIG is empty, removes LD_LIBRARY_PATH."""
    env = {"LD_LIBRARY_PATH": "/pyinstaller", "LD_LIBRARY_PATH_ORIG": ""}
    assert "LD_LIBRARY_PATH" not in sanitized_env(env)


# ---------------------------------------------------------------------------
# execute_command logging redaction
# ---------------------------------------------------------------------------


class TestExecuteCommandLoggingRedaction:
    """Tests for sensitive value redaction in execute_command logging."""

    def test_logs_command_without_errors(self, caplog):
        """Command logging with redaction doesn't raise errors."""
        with patch("subprocess.Popen") as mock_popen:
            mock_process = mock_popen.return_value
            mock_process.stdout = None
            mock_process.stderr = None

            cmd = ["docker", "run", "-e", "LMNR_PROJECT_API_KEY=secret123", "image"]

            try:
                execute_command(cmd)
            except RuntimeError:
                # Logging should happen even if subprocess fails
                pass

            # Command should be logged
            assert "docker" in caplog.text
            assert "run" in caplog.text
            assert "image" in caplog.text

    def test_redacts_api_key_from_string_command(self):
        """API keys in string commands are properly redacted."""
        from openhands.sdk.utils.redact import redact_text_secrets

        # Test the redaction function directly
        # Valid Anthropic key format: sk-ant-api[2 digits]-[20+ chars]
        cmd_str = "curl -H 'Authorization: sk-ant-api00-abcd1234567890abcdefghijklmnop' https://api.anthropic.com"
        redacted = redact_text_secrets(cmd_str)

        # The secret should be redacted in the output of the function
        assert "sk-ant-api00-abcd1234567890abcdefghijklmnop" not in redacted
        assert "<redacted>" in redacted
        # Command structure should be preserved
        assert "curl" in redacted
        assert "https://api.anthropic.com" in redacted

    def test_redacts_key_value_env_format(self):
        """KEY=VALUE environment variable format is redacted."""
        from openhands.sdk.utils.redact import redact_text_secrets

        cmd_str = "docker run -e api_key='secretvalue123456789' -e DEBUG=true image"
        redacted = redact_text_secrets(cmd_str)

        # api_key value should be redacted
        assert "secretvalue123456789" not in redacted
        # But non-sensitive DEBUG value should be present
        assert "DEBUG" in redacted
        # Command structure preserved
        assert "docker" in redacted

    def test_preserves_non_sensitive_args(self, caplog):
        """Non-sensitive arguments are preserved in logs."""
        with patch("subprocess.Popen") as mock_popen:
            mock_process = mock_popen.return_value
            mock_process.stdout = None
            mock_process.stderr = None

            cmd = ["docker", "run", "-e", "DEBUG=true", "image:latest"]

            try:
                execute_command(cmd)
            except RuntimeError:
                pass

            # Non-sensitive values should be visible
            assert "DEBUG=true" in caplog.text
            assert "image:latest" in caplog.text
            assert "docker" in caplog.text
