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


def test_post_github_comment_success():
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


def test_post_github_comment_missing_token():
    """Test that missing GITHUB_TOKEN raises error."""
    from agent_script import post_github_comment  # type: ignore[import-not-found]

    with (
        patch.dict("os.environ", {}, clear=True),
        pytest.raises(ValueError, match="GITHUB_TOKEN"),
    ):
        post_github_comment("owner/repo", "123", "Test comment")


def test_cloud_mode_instruction_format():
    """Test that CLOUD_MODE_INSTRUCTION can be formatted correctly."""
    from agent_script import (  # type: ignore[import-not-found]
        CLOUD_MODE_INSTRUCTION,
    )

    # Test that the instruction can be formatted without errors
    formatted = CLOUD_MODE_INSTRUCTION.format(
        repo_name="owner/repo",
        pr_number="123",
    )

    # Verify the formatted instruction contains the expected values
    assert "owner/repo" in formatted
    assert "123" in formatted
    assert "GITHUB_TOKEN" in formatted
    assert "curl" in formatted


def test_mode_defaults_to_sdk():
    """Test that MODE defaults to 'sdk'."""
    import os

    # When MODE is not set, it should default to 'sdk'
    with patch.dict("os.environ", {}, clear=False):
        mode = os.getenv("MODE", "sdk").lower()
        assert mode == "sdk"


def test_mode_cloud_accepted():
    """Test that 'cloud' is a valid MODE."""
    import os

    with patch.dict("os.environ", {"MODE": "cloud"}, clear=False):
        mode = os.getenv("MODE", "sdk").lower()
        assert mode == "cloud"


def test_mode_case_insensitive():
    """Test that MODE is case insensitive."""
    import os

    for value in ["CLOUD", "Cloud", "cLoUd"]:
        with patch.dict("os.environ", {"MODE": value}, clear=False):
            mode = os.getenv("MODE", "sdk").lower()
            assert mode == "cloud"


def test_sdk_mode_requires_llm_api_key():
    """Test that SDK mode fails without LLM_API_KEY."""
    from agent_script import main  # type: ignore[import-not-found]

    # Set up minimal environment for SDK mode but missing LLM_API_KEY
    env = {
        "MODE": "sdk",
        "GITHUB_TOKEN": "test-token",
        "PR_NUMBER": "123",
        "PR_TITLE": "Test PR",
        "PR_BASE_BRANCH": "main",
        "PR_HEAD_BRANCH": "feature",
        "REPO_NAME": "owner/repo",
        # LLM_API_KEY intentionally missing
    }

    with (
        patch.dict("os.environ", env, clear=True),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert exc_info.value.code == 1


def test_cloud_mode_requires_cloud_api_key():
    """Test that cloud mode fails without OPENHANDS_CLOUD_API_KEY."""
    from agent_script import main  # type: ignore[import-not-found]

    # Set up environment for cloud mode but missing OPENHANDS_CLOUD_API_KEY
    # Note: Cloud mode now also requires LLM_API_KEY
    env = {
        "MODE": "cloud",
        "GITHUB_TOKEN": "test-token",
        "LLM_API_KEY": "test-llm-key",
        "PR_NUMBER": "123",
        "PR_TITLE": "Test PR",
        "PR_BASE_BRANCH": "main",
        "PR_HEAD_BRANCH": "feature",
        "REPO_NAME": "owner/repo",
        # OPENHANDS_CLOUD_API_KEY intentionally missing
    }

    with (
        patch.dict("os.environ", env, clear=True),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert exc_info.value.code == 1


def test_cloud_mode_requires_llm_api_key():
    """Test that cloud mode also fails without LLM_API_KEY."""
    from agent_script import main  # type: ignore[import-not-found]

    # Cloud mode requires both OPENHANDS_CLOUD_API_KEY and LLM_API_KEY
    env = {
        "MODE": "cloud",
        "GITHUB_TOKEN": "test-token",
        "OPENHANDS_CLOUD_API_KEY": "test-cloud-key",
        "PR_NUMBER": "123",
        "PR_TITLE": "Test PR",
        "PR_BASE_BRANCH": "main",
        "PR_HEAD_BRANCH": "feature",
        "REPO_NAME": "owner/repo",
        # LLM_API_KEY intentionally missing
    }

    with (
        patch.dict("os.environ", env, clear=True),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert exc_info.value.code == 1


def test_both_modes_require_github_token():
    """Test that both modes require GITHUB_TOKEN."""
    from agent_script import main  # type: ignore[import-not-found]

    # Test SDK mode without GITHUB_TOKEN
    sdk_env = {
        "MODE": "sdk",
        "LLM_API_KEY": "test-key",
        "PR_NUMBER": "123",
        "PR_TITLE": "Test PR",
        "PR_BASE_BRANCH": "main",
        "PR_HEAD_BRANCH": "feature",
        "REPO_NAME": "owner/repo",
        # GITHUB_TOKEN intentionally missing
    }

    with (
        patch.dict("os.environ", sdk_env, clear=True),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert exc_info.value.code == 1

    # Test cloud mode without GITHUB_TOKEN
    cloud_env = {
        "MODE": "cloud",
        "OPENHANDS_CLOUD_API_KEY": "test-key",
        "LLM_API_KEY": "test-llm-key",
        "PR_NUMBER": "123",
        "PR_TITLE": "Test PR",
        "PR_BASE_BRANCH": "main",
        "PR_HEAD_BRANCH": "feature",
        "REPO_NAME": "owner/repo",
        # GITHUB_TOKEN intentionally missing
    }

    with (
        patch.dict("os.environ", cloud_env, clear=True),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert exc_info.value.code == 1
