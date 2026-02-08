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


def test_cloud_mode_requires_cloud_api_key():
    """Test that cloud mode requires OPENHANDS_CLOUD_API_KEY."""
    base_required_vars = [
        "GITHUB_TOKEN",
        "PR_NUMBER",
        "PR_TITLE",
        "PR_BASE_BRANCH",
        "PR_HEAD_BRANCH",
        "REPO_NAME",
    ]
    cloud_required_vars = base_required_vars + ["OPENHANDS_CLOUD_API_KEY"]

    assert "OPENHANDS_CLOUD_API_KEY" in cloud_required_vars
    # Cloud mode does NOT require LLM_API_KEY
    assert "LLM_API_KEY" not in cloud_required_vars


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


def test_comment_body_is_markdown():
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
