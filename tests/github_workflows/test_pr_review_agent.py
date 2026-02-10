"""Tests for PR review agent script.

Note: This test file uses sys.path manipulation to import agent_script from the
examples directory. The pyright "reportMissingImports" errors are expected and
suppressed with type: ignore comments.
"""

import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# Add the PR review example directory to the path for imports
pr_review_path = (
    Path(__file__).parent.parent.parent
    / "examples"
    / "03_github_workflows"
    / "02_pr_review"
)
sys.path.insert(0, str(pr_review_path))


class TestMakeHttpRequest:
    """Tests for the _make_http_request helper function."""

    def test_get_request_success(self):
        """Test successful GET request."""
        from agent_script import _make_http_request  # type: ignore[import-not-found]

        mock_response = MagicMock()
        mock_response.read.return_value = b'{"status": "ok"}'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch(
            "urllib.request.urlopen", return_value=mock_response
        ) as mock_urlopen:
            result = _make_http_request(
                "https://api.example.com/test",
                headers={"Authorization": "Bearer token"},
            )

            assert result == b'{"status": "ok"}'
            mock_urlopen.assert_called_once()
            request = mock_urlopen.call_args[0][0]
            assert request.full_url == "https://api.example.com/test"
            assert request.get_header("Authorization") == "Bearer token"

    def test_post_request_with_json_data(self):
        """Test POST request with JSON data."""
        from agent_script import _make_http_request  # type: ignore[import-not-found]

        mock_response = MagicMock()
        mock_response.read.return_value = b'{"id": 123}'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch(
            "urllib.request.urlopen", return_value=mock_response
        ) as mock_urlopen:
            result = _make_http_request(
                "https://api.example.com/create",
                method="POST",
                data={"name": "test"},
            )

            assert result == b'{"id": 123}'
            request = mock_urlopen.call_args[0][0]
            assert request.get_method() == "POST"
            assert request.data == json.dumps({"name": "test"}).encode("utf-8")
            assert request.get_header("Content-type") == "application/json"

    def test_http_error_handling(self):
        """Test that HTTP errors are converted to RuntimeError."""
        from agent_script import _make_http_request  # type: ignore[import-not-found]

        mock_error = urllib.error.HTTPError(
            "https://api.example.com/test",
            404,
            "Not Found",
            {},  # type: ignore[arg-type]
            None,
        )
        mock_error.read = MagicMock(return_value=b"Resource not found")

        with (
            patch("urllib.request.urlopen", side_effect=mock_error),
            pytest.raises(RuntimeError, match="Test API failed: HTTP 404"),
        ):
            _make_http_request(
                "https://api.example.com/test",
                error_prefix="Test API",
            )


class TestPostGithubComment:
    """Tests for the post_github_comment function."""

    def test_success(self):
        """Test successful comment posting."""
        from agent_script import post_github_comment  # type: ignore[import-not-found]

        mock_response = MagicMock()
        mock_response.read.return_value = b'{"id": 123}'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with (
            patch.dict("os.environ", {"GITHUB_TOKEN": "test-token"}, clear=False),
            patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen,
        ):
            post_github_comment("owner/repo", "123", "Test comment body")

            mock_urlopen.assert_called_once()
            request = mock_urlopen.call_args[0][0]
            assert request.full_url == (
                "https://api.github.com/repos/owner/repo/issues/123/comments"
            )
            assert request.get_header("Authorization") == "Bearer test-token"

    def test_missing_token_raises_error(self):
        """Test that missing GITHUB_TOKEN raises ValueError."""
        from agent_script import post_github_comment  # type: ignore[import-not-found]

        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(ValueError, match="GITHUB_TOKEN"),
        ):
            post_github_comment("owner/repo", "123", "Test comment")


class TestStartCloudConversation:
    """Tests for the _start_cloud_conversation function."""

    def test_success(self):
        """Test successful cloud conversation creation."""
        from agent_script import (  # type: ignore[import-not-found]
            _start_cloud_conversation,
        )

        mock_response = MagicMock()
        mock_response.read.return_value = b'{"conversation_id": "abc123"}'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            conv_id, conv_url = _start_cloud_conversation(
                "https://app.all-hands.dev",
                "test-api-key",
                "Hello, review this PR",
            )

            assert conv_id == "abc123"
            assert conv_url == "https://app.all-hands.dev/conversations/abc123"

    def test_missing_conversation_id_raises_error(self):
        """Test that missing conversation_id in response raises RuntimeError."""
        from agent_script import (  # type: ignore[import-not-found]
            _start_cloud_conversation,
        )

        mock_response = MagicMock()
        mock_response.read.return_value = b'{"error": "something went wrong"}'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with (
            patch("urllib.request.urlopen", return_value=mock_response),
            pytest.raises(RuntimeError, match="No conversation_id in response"),
        ):
            _start_cloud_conversation(
                "https://app.all-hands.dev",
                "test-api-key",
                "Hello",
            )


class TestCloudModePrompt:
    """Tests for the CLOUD_MODE_PROMPT template."""

    def test_format_with_all_fields(self):
        """Test that CLOUD_MODE_PROMPT formats correctly with all fields."""
        from agent_script import CLOUD_MODE_PROMPT  # type: ignore[import-not-found]

        formatted = CLOUD_MODE_PROMPT.format(
            skill_trigger="/codereview",
            repo_name="owner/repo",
            pr_number="123",
            title="Test PR",
            body="Test body",
            base_branch="main",
            head_branch="feature",
        )

        assert "/codereview" in formatted
        assert "owner/repo" in formatted
        assert "123" in formatted
        assert "Test PR" in formatted
        assert "gh pr diff" in formatted


class TestGetRequiredVarsForMode:
    """Tests for the _get_required_vars_for_mode function."""

    def test_sdk_mode_requires_llm_api_key(self):
        """Test that SDK mode requires LLM_API_KEY."""
        from agent_script import (  # type: ignore[import-not-found]
            _get_required_vars_for_mode,
        )

        vars = _get_required_vars_for_mode("sdk")
        assert "LLM_API_KEY" in vars
        assert "OPENHANDS_CLOUD_API_KEY" not in vars

    def test_cloud_mode_requires_cloud_api_key(self):
        """Test that cloud mode requires OPENHANDS_CLOUD_API_KEY."""
        from agent_script import (  # type: ignore[import-not-found]
            _get_required_vars_for_mode,
        )

        vars = _get_required_vars_for_mode("cloud")
        assert "OPENHANDS_CLOUD_API_KEY" in vars
        assert "LLM_API_KEY" not in vars

    def test_both_modes_require_github_token(self):
        """Test that both modes require GITHUB_TOKEN."""
        from agent_script import (  # type: ignore[import-not-found]
            _get_required_vars_for_mode,
        )

        sdk_vars = _get_required_vars_for_mode("sdk")
        cloud_vars = _get_required_vars_for_mode("cloud")

        assert "GITHUB_TOKEN" in sdk_vars
        assert "GITHUB_TOKEN" in cloud_vars


class TestGetPrInfo:
    """Tests for the _get_pr_info function."""

    def test_returns_pr_info_from_env(self):
        """Test that _get_pr_info returns PRInfo from environment."""
        from agent_script import _get_pr_info  # type: ignore[import-not-found]

        env = {
            "PR_NUMBER": "42",
            "PR_TITLE": "Fix bug",
            "PR_BODY": "This fixes the bug",
            "REPO_NAME": "owner/repo",
            "PR_BASE_BRANCH": "main",
            "PR_HEAD_BRANCH": "fix-branch",
        }

        with patch.dict("os.environ", env, clear=False):
            pr_info = _get_pr_info()

            assert pr_info["number"] == "42"
            assert pr_info["title"] == "Fix bug"
            assert pr_info["body"] == "This fixes the bug"
            assert pr_info["repo_name"] == "owner/repo"
            assert pr_info["base_branch"] == "main"
            assert pr_info["head_branch"] == "fix-branch"


class TestMainValidation:
    """Tests for main() environment validation."""

    def test_sdk_mode_fails_without_llm_api_key(self):
        """Test that SDK mode fails without LLM_API_KEY."""
        from agent_script import main  # type: ignore[import-not-found]

        env = {
            "MODE": "sdk",
            "GITHUB_TOKEN": "test-token",
            "PR_NUMBER": "123",
            "PR_TITLE": "Test PR",
            "PR_BASE_BRANCH": "main",
            "PR_HEAD_BRANCH": "feature",
            "REPO_NAME": "owner/repo",
        }

        with (
            patch.dict("os.environ", env, clear=True),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1

    def test_cloud_mode_fails_without_cloud_api_key(self):
        """Test that cloud mode fails without OPENHANDS_CLOUD_API_KEY."""
        from agent_script import main  # type: ignore[import-not-found]

        env = {
            "MODE": "cloud",
            "GITHUB_TOKEN": "test-token",
            "PR_NUMBER": "123",
            "PR_TITLE": "Test PR",
            "PR_BASE_BRANCH": "main",
            "PR_HEAD_BRANCH": "feature",
            "REPO_NAME": "owner/repo",
        }

        with (
            patch.dict("os.environ", env, clear=True),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1

    def test_both_modes_fail_without_github_token(self):
        """Test that both modes fail without GITHUB_TOKEN."""
        from agent_script import main  # type: ignore[import-not-found]

        sdk_env = {
            "MODE": "sdk",
            "LLM_API_KEY": "test-key",
            "PR_NUMBER": "123",
            "PR_TITLE": "Test PR",
            "PR_BASE_BRANCH": "main",
            "PR_HEAD_BRANCH": "feature",
            "REPO_NAME": "owner/repo",
        }

        with (
            patch.dict("os.environ", sdk_env, clear=True),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1

        cloud_env = {
            "MODE": "cloud",
            "OPENHANDS_CLOUD_API_KEY": "test-key",
            "PR_NUMBER": "123",
            "PR_TITLE": "Test PR",
            "PR_BASE_BRANCH": "main",
            "PR_HEAD_BRANCH": "feature",
            "REPO_NAME": "owner/repo",
        }

        with (
            patch.dict("os.environ", cloud_env, clear=True),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1
