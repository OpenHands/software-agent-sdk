"""Tests for PR review agent script.

Note: This test file uses sys.path manipulation to import agent_script from the
examples directory. The pyright "reportMissingImports" errors are expected and
suppressed with type: ignore comments.
"""

import sys
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


class TestPostGithubComment:
    """Tests for the _post_github_comment function in cloud_mode."""

    def test_success(self):
        """Test successful comment posting."""
        from utils.cloud_mode import (  # type: ignore[import-not-found]
            _post_github_comment,
        )

        mock_response = MagicMock()
        mock_response.read.return_value = b'{"id": 1}'

        with (
            patch.dict("os.environ", {"GITHUB_TOKEN": "test-token"}, clear=False),
            patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen,
        ):
            _post_github_comment("owner/repo", "123", "Test comment body")

            mock_urlopen.assert_called_once()

    def test_missing_token_raises_error(self):
        """Test that missing GITHUB_TOKEN raises ValueError."""
        from utils.cloud_mode import (  # type: ignore[import-not-found]
            _post_github_comment,
        )

        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(ValueError, match="GITHUB_TOKEN"),
        ):
            _post_github_comment("owner/repo", "123", "Test comment")


class TestGetPrDiffViaGithubApi:
    """Tests for the get_pr_diff_via_github_api function."""

    def test_success(self):
        """Test successful diff fetching."""
        from agent_script import (  # type: ignore[import-not-found]
            get_pr_diff_via_github_api,
        )

        mock_response = MagicMock()
        mock_response.read.return_value = b"diff --git a/file.py b/file.py\n+new line"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        env = {"REPO_NAME": "owner/repo", "GITHUB_TOKEN": "test-token"}

        with (
            patch.dict("os.environ", env, clear=False),
            patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen,
        ):
            result = get_pr_diff_via_github_api("123")

            assert result == "diff --git a/file.py b/file.py\n+new line"
            mock_urlopen.assert_called_once()


class TestRunCloudMode:
    """Tests for run_agent_review in cloud_mode using OpenHandsCloudWorkspace."""

    def test_cloud_mode_does_not_require_llm_api_key(self):
        """Test that cloud mode does NOT require LLM_API_KEY (uses cloud's LLM)."""
        from agent_script import (  # type: ignore[import-not-found]
            _get_required_vars_for_mode,
        )

        vars = _get_required_vars_for_mode("cloud")
        assert "LLM_API_KEY" not in vars
        assert "OPENHANDS_CLOUD_API_KEY" in vars


class TestCloudModePrompt:
    """Tests for the CLOUD_MODE_PROMPT template in cloud_mode."""

    def test_format_with_all_fields(self):
        """Test that CLOUD_MODE_PROMPT formats correctly with all fields."""
        from utils.cloud_mode import (  # type: ignore[import-not-found]
            CLOUD_MODE_PROMPT,
        )

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

    def test_cloud_mode_requires_only_cloud_api_key(self):
        """Test that cloud mode requires OPENHANDS_CLOUD_API_KEY but not LLM_API_KEY."""
        from agent_script import (  # type: ignore[import-not-found]
            _get_required_vars_for_mode,
        )

        vars = _get_required_vars_for_mode("cloud")
        assert "OPENHANDS_CLOUD_API_KEY" in vars
        # Cloud mode uses the user's LLM config from OpenHands Cloud,
        # so LLM_API_KEY is optional
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

        # Note: LLM_API_KEY is optional for cloud mode, so we don't include it
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

        # Note: LLM_API_KEY is optional for cloud mode
        cloud_env = {
            "MODE": "cloud",
            "OPENHANDS_CLOUD_API_KEY": "test-cloud-key",
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
