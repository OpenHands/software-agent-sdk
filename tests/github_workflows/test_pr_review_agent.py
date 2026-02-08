"""Tests for PR review agent script."""

import json
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


def test_start_cloud_conversation_success():
    """Test successful cloud conversation creation."""
    from agent_script import (  # type: ignore[import-not-found]
        _start_cloud_conversation,
    )

    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(
        {"conversation_id": "test-conv-123"}
    ).encode("utf-8")
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
        result = _start_cloud_conversation(
            cloud_api_url="https://app.all-hands.dev",
            cloud_api_key="test-cloud-key",
            prompt="Test prompt",
            github_token="test-github-token",
        )

        assert result == "test-conv-123"
        mock_urlopen.assert_called_once()
        call_args = mock_urlopen.call_args
        request = call_args[0][0]

        assert request.full_url == "https://app.all-hands.dev/api/conversations"
        assert request.get_header("Authorization") == "Bearer test-cloud-key"
        assert request.get_header("Content-type") == "application/json"


def test_start_cloud_conversation_with_id_field():
    """Test cloud conversation handles 'id' field in response."""
    from agent_script import (  # type: ignore[import-not-found]
        _start_cloud_conversation,
    )

    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({"id": "conv-456"}).encode("utf-8")
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        result = _start_cloud_conversation(
            cloud_api_url="https://app.all-hands.dev",
            cloud_api_key="test-key",
            prompt="Test",
        )

        assert result == "conv-456"


def test_start_cloud_conversation_missing_id():
    """Test cloud conversation raises error when response missing conversation_id."""
    from agent_script import (  # type: ignore[import-not-found]
        _start_cloud_conversation,
    )

    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({"status": "ok"}).encode("utf-8")
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with (
        patch("urllib.request.urlopen", return_value=mock_response),
        pytest.raises(RuntimeError, match="missing conversation_id"),
    ):
        _start_cloud_conversation(
            cloud_api_url="https://app.all-hands.dev",
            cloud_api_key="test-key",
            prompt="Test",
        )


def test_prepare_review_context():
    """Test that _prepare_review_context returns correct prompt and skill trigger."""
    from agent_script import (  # type: ignore[import-not-found]
        _prepare_review_context,
    )

    pr_info = {
        "number": "123",
        "title": "Test PR",
        "body": "Test body",
        "repo_name": "owner/repo",
        "base_branch": "main",
        "head_branch": "feature",
    }

    # Mock the functions that _prepare_review_context calls
    with (
        patch.dict(
            "os.environ",
            {
                "GITHUB_TOKEN": "test-token",
                "REPO_NAME": "owner/repo",
                "PR_NUMBER": "123",
            },
            clear=False,
        ),
        patch("agent_script.get_truncated_pr_diff", return_value="mock diff content"),
        patch("agent_script.get_head_commit_sha", return_value="abc123"),
    ):
        # Test standard review style
        prompt, skill_trigger = _prepare_review_context(pr_info, "standard")
        assert skill_trigger == "/codereview"
        assert "Test PR" in prompt
        assert "mock diff content" in prompt

        # Test roasted review style
        prompt, skill_trigger = _prepare_review_context(pr_info, "roasted")
        assert skill_trigger == "/codereview-roasted"


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

    # Set up minimal environment for cloud mode but missing OPENHANDS_CLOUD_API_KEY
    env = {
        "MODE": "cloud",
        "GITHUB_TOKEN": "test-token",
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


def test_conversation_url_format():
    """Test that conversation URL is correctly formatted."""
    cloud_api_url = "https://app.all-hands.dev"
    conversation_id = "12345678-1234-1234-1234-123456789abc"

    expected_url = f"{cloud_api_url}/conversations/{conversation_id}"
    assert expected_url == (
        "https://app.all-hands.dev/conversations/12345678-1234-1234-1234-123456789abc"
    )


def test_conversation_url_with_custom_api_url():
    """Test conversation URL with custom cloud API URL."""
    cloud_api_url = "https://custom.openhands.dev"
    conversation_id = "test-conversation-id"

    expected_url = f"{cloud_api_url}/conversations/{conversation_id}"
    assert expected_url == (
        "https://custom.openhands.dev/conversations/test-conversation-id"
    )


def test_comment_body_contains_url():
    """Test that comment body template contains the conversation URL."""
    from agent_script import (  # type: ignore[import-not-found]
        CLOUD_REVIEW_COMMENT_TEMPLATE,
    )

    conversation_url = "https://app.all-hands.dev/conversations/test-id"
    comment_body = CLOUD_REVIEW_COMMENT_TEMPLATE.format(
        conversation_url=conversation_url
    )

    assert conversation_url in comment_body
    assert "OpenHands PR Review Started" in comment_body
    assert "Track progress here" in comment_body


def test_comment_body_is_markdown():
    """Test that comment body template uses markdown formatting."""
    from agent_script import (  # type: ignore[import-not-found]
        CLOUD_REVIEW_COMMENT_TEMPLATE,
    )

    conversation_url = "https://app.all-hands.dev/conversations/test-id"
    comment_body = CLOUD_REVIEW_COMMENT_TEMPLATE.format(
        conversation_url=conversation_url
    )

    # Check for markdown bold syntax
    assert "**OpenHands PR Review Started**" in comment_body
    assert "**Track progress here:**" in comment_body
    # Check for markdown link syntax
    assert f"[{conversation_url}]({conversation_url})" in comment_body
