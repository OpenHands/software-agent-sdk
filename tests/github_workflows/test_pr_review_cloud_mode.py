"""Tests for PR review cloud mode support."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# Import the PR review functions
pr_review_path = (
    Path(__file__).parent.parent.parent
    / "examples"
    / "03_github_workflows"
    / "02_pr_review"
)
sys.path.insert(0, str(pr_review_path))
from agent_script import (  # noqa: E402  # type: ignore[import-not-found]
    _build_agent,
    _build_llm,
    _build_secrets,
    main,
)


# Minimal env vars shared by both modes
_BASE_ENV = {
    "GITHUB_TOKEN": "ghp_test",
    "LLM_API_KEY": "sk-test",
    "PR_NUMBER": "42",
    "PR_TITLE": "Test PR",
    "PR_BASE_BRANCH": "main",
    "PR_HEAD_BRANCH": "feature",
    "REPO_NAME": "owner/repo",
    "REVIEW_STYLE": "standard",
}


def test_main_rejects_unknown_mode():
    """main() exits with error on unknown MODE."""
    env = {**_BASE_ENV, "MODE": "invalid"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(SystemExit):
            main()


def test_main_requires_llm_api_key():
    """Both modes require LLM_API_KEY."""
    env = {**_BASE_ENV, "MODE": "local"}
    del env["LLM_API_KEY"]
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(SystemExit):
            main()


def test_main_cloud_mode_requires_openhands_api_key():
    """Cloud mode requires OPENHANDS_API_KEY."""
    env = {**_BASE_ENV, "MODE": "cloud"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(SystemExit):
            main()


def test_main_cloud_mode_requires_llm_api_key():
    """Cloud mode also requires LLM_API_KEY (limitation pending OpenHands#13268)."""
    env = {**_BASE_ENV, "MODE": "cloud", "OPENHANDS_API_KEY": "oh-key"}
    del env["LLM_API_KEY"]
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(SystemExit):
            main()


def test_main_cloud_mode_dispatches_with_github_token():
    """Cloud mode dispatches to _run_cloud_mode with github_token."""
    env = {**_BASE_ENV, "MODE": "cloud", "OPENHANDS_API_KEY": "oh-key"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("agent_script.get_truncated_pr_diff", return_value="diff"),
        patch("agent_script.get_head_commit_sha", return_value="abc123"),
        patch("agent_script.get_pr_review_context", return_value=""),
        patch("agent_script._run_cloud_mode") as mock_cloud,
    ):
        main()
        mock_cloud.assert_called_once()
        call_kwargs = mock_cloud.call_args[1]
        assert "prompt" in call_kwargs
        assert call_kwargs["github_token"] == "ghp_test"


def test_main_local_mode_dispatches_to_local():
    """Local mode dispatches to _run_local_mode."""
    env = {**_BASE_ENV, "MODE": "local"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("agent_script.get_truncated_pr_diff", return_value="diff"),
        patch("agent_script.get_head_commit_sha", return_value="abc123"),
        patch("agent_script.get_pr_review_context", return_value=""),
        patch("agent_script._run_local_mode") as mock_local,
    ):
        main()
        mock_local.assert_called_once()


def test_main_default_mode_is_local():
    """When MODE is not set, default to local mode."""
    env = {**_BASE_ENV}
    env.pop("MODE", None)
    with (
        patch.dict(os.environ, env, clear=True),
        patch("agent_script.get_truncated_pr_diff", return_value="diff"),
        patch("agent_script.get_head_commit_sha", return_value="abc123"),
        patch("agent_script.get_pr_review_context", return_value=""),
        patch("agent_script._run_local_mode") as mock_local,
    ):
        main()
        mock_local.assert_called_once()


def test_build_agent_returns_agent():
    """_build_agent returns an Agent with expected configuration."""
    from openhands.sdk import LLM

    llm = LLM(model="test-model", usage_id="test", drop_params=True)

    with patch("agent_script.load_project_skills", return_value=[]):
        agent = _build_agent(llm, "/tmp/test")

    assert agent.llm.model == "test-model"
    assert len(agent.tools) > 0  # Default tools loaded


def test_build_llm_defaults():
    """_build_llm uses defaults when no env vars are set."""
    with patch.dict(os.environ, {}, clear=True):
        llm = _build_llm()

    assert llm.model == "anthropic/claude-sonnet-4-5-20250929"
    assert llm.api_key is None
    assert llm.base_url is None


def test_build_llm_with_api_key_and_base_url():
    """_build_llm picks up LLM_API_KEY and LLM_BASE_URL."""
    env = {
        "LLM_MODEL": "anthropic/claude-sonnet-4-5-20250929",
        "LLM_API_KEY": "sk-test",
        "LLM_BASE_URL": "https://custom.llm",
    }
    with patch.dict(os.environ, env, clear=True):
        llm = _build_llm()

    assert llm.model == "anthropic/claude-sonnet-4-5-20250929"
    assert llm.api_key is not None
    assert llm.api_key.get_secret_value() == "sk-test"
    assert llm.base_url == "https://custom.llm"


def test_build_secrets_with_all_keys():
    """_build_secrets includes GITHUB_TOKEN and LLM_API_KEY when present."""
    env = {"LLM_API_KEY": "sk-key"}
    with patch.dict(os.environ, env, clear=True):
        secrets = _build_secrets("ghp_token")

    assert secrets == {
        "LLM_API_KEY": "sk-key",
        "GITHUB_TOKEN": "ghp_token",
    }


def test_build_secrets_without_optional_keys():
    """_build_secrets returns empty dict without LLM_API_KEY or github_token."""
    with patch.dict(os.environ, {}, clear=True):
        secrets = _build_secrets(None)

    assert secrets == {}
