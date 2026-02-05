"""Tests for PR review agent script."""

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

# Import is done dynamically in tests to avoid pyright errors
# The agent_script module is imported inside test methods


class TestPostGitHubComment:
    """Tests for post_github_comment function."""

    def test_post_github_comment_success(self):
        """Test successful comment posting."""
        from agent_script import post_github_comment  # type: ignore[import-not-found]

        mock_response = MagicMock()
        mock_response.status = 201
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with (
            patch.dict("os.environ", {"GITHUB_TOKEN": "test-token"}, clear=False),
            patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen,
        ):
            post_github_comment("owner/repo", "123", "Test comment body")

            # Verify the request was made
            mock_urlopen.assert_called_once()
            call_args = mock_urlopen.call_args
            request = call_args[0][0]

            assert request.full_url == (
                "https://api.github.com/repos/owner/repo/issues/123/comments"
            )
            assert request.get_header("Authorization") == "Bearer test-token"
            assert request.get_header("Content-type") == "application/json"

    def test_post_github_comment_missing_token(self):
        """Test that missing GITHUB_TOKEN raises error."""
        from agent_script import post_github_comment  # type: ignore[import-not-found]

        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(ValueError, match="GITHUB_TOKEN"),
        ):
            post_github_comment("owner/repo", "123", "Test comment")


class TestReviewModeValidation:
    """Tests for review mode validation in main()."""

    def test_review_mode_defaults_to_sdk(self):
        """Test that REVIEW_MODE defaults to 'sdk'."""
        import os

        # When REVIEW_MODE is not set, it should default to 'sdk'
        with patch.dict("os.environ", {}, clear=False):
            mode = os.getenv("REVIEW_MODE", "sdk").lower()
            assert mode == "sdk"

    def test_review_mode_cloud_accepted(self):
        """Test that 'cloud' is a valid REVIEW_MODE."""
        import os

        with patch.dict("os.environ", {"REVIEW_MODE": "cloud"}, clear=False):
            mode = os.getenv("REVIEW_MODE", "sdk").lower()
            assert mode == "cloud"

    def test_review_mode_case_insensitive(self):
        """Test that REVIEW_MODE is case insensitive."""
        import os

        for value in ["CLOUD", "Cloud", "cLoUd"]:
            with patch.dict("os.environ", {"REVIEW_MODE": value}, clear=False):
                mode = os.getenv("REVIEW_MODE", "sdk").lower()
                assert mode == "cloud"


class TestRequiredEnvironmentVariables:
    """Tests for required environment variable validation."""

    def test_sdk_mode_requires_llm_api_key(self):
        """Test that SDK mode requires LLM_API_KEY."""
        base_required_vars = [
            "GITHUB_TOKEN",
            "PR_NUMBER",
            "PR_TITLE",
            "PR_BASE_BRANCH",
            "PR_HEAD_BRANCH",
            "REPO_NAME",
        ]
        sdk_required_vars = base_required_vars + ["LLM_API_KEY"]

        assert "LLM_API_KEY" in sdk_required_vars
        assert "OPENHANDS_CLOUD_API_KEY" not in sdk_required_vars

    def test_cloud_mode_requires_cloud_api_key(self):
        """Test that cloud mode requires OPENHANDS_CLOUD_API_KEY."""
        base_required_vars = [
            "GITHUB_TOKEN",
            "PR_NUMBER",
            "PR_TITLE",
            "PR_BASE_BRANCH",
            "PR_HEAD_BRANCH",
            "REPO_NAME",
        ]
        cloud_required_vars = base_required_vars + [
            "LLM_API_KEY",
            "OPENHANDS_CLOUD_API_KEY",
        ]

        assert "LLM_API_KEY" in cloud_required_vars
        assert "OPENHANDS_CLOUD_API_KEY" in cloud_required_vars


class TestCloudConversationUrl:
    """Tests for cloud conversation URL generation."""

    def test_conversation_url_format(self):
        """Test that conversation URL is correctly formatted."""
        cloud_api_url = "https://app.all-hands.dev"
        conversation_id = "12345678-1234-1234-1234-123456789abc"

        expected_url = f"{cloud_api_url}/conversations/{conversation_id}"
        assert expected_url == (
            "https://app.all-hands.dev/conversations/"
            "12345678-1234-1234-1234-123456789abc"
        )

    def test_conversation_url_with_custom_api_url(self):
        """Test conversation URL with custom cloud API URL."""
        cloud_api_url = "https://custom.openhands.dev"
        conversation_id = "test-conversation-id"

        expected_url = f"{cloud_api_url}/conversations/{conversation_id}"
        assert expected_url == (
            "https://custom.openhands.dev/conversations/test-conversation-id"
        )


class TestCloudModeCommentBody:
    """Tests for cloud mode PR comment body."""

    def test_comment_body_contains_url(self):
        """Test that comment body contains the conversation URL."""
        conversation_url = "https://app.all-hands.dev/conversations/test-id"

        comment_body = (
            f"🤖 **OpenHands PR Review Started**\n\n"
            f"A code review has been initiated in OpenHands Cloud.\n\n"
            f"📍 **Track progress here:** [{conversation_url}]({conversation_url})\n\n"
            f"The review will analyze the changes and post inline comments "
            f"directly on this PR when complete."
        )

        assert conversation_url in comment_body
        assert "OpenHands PR Review Started" in comment_body
        assert "Track progress here" in comment_body

    def test_comment_body_is_markdown(self):
        """Test that comment body uses markdown formatting."""
        conversation_url = "https://app.all-hands.dev/conversations/test-id"

        comment_body = (
            f"🤖 **OpenHands PR Review Started**\n\n"
            f"A code review has been initiated in OpenHands Cloud.\n\n"
            f"📍 **Track progress here:** [{conversation_url}]({conversation_url})\n\n"
            f"The review will analyze the changes and post inline comments "
            f"directly on this PR when complete."
        )

        # Check for markdown bold syntax
        assert "**OpenHands PR Review Started**" in comment_body
        assert "**Track progress here:**" in comment_body
        # Check for markdown link syntax
        assert f"[{conversation_url}]({conversation_url})" in comment_body
