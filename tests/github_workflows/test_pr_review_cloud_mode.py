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
    main,
)


# Minimal env vars shared by both modes (everything except mode-specific keys)
_BASE_ENV = {
    "GITHUB_TOKEN": "ghp_test",
    "PR_NUMBER": "42",
    "PR_TITLE": "Test PR",
    "PR_BASE_BRANCH": "main",
    "PR_HEAD_BRANCH": "feature",
    "REPO_NAME": "owner/repo",
    "REVIEW_STYLE": "standard",
}


def test_main_rejects_unknown_mode():
    """main() exits with error on unknown MODE."""
    env = {**_BASE_ENV, "MODE": "invalid", "LLM_API_KEY": "key"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(SystemExit):
            main()


def test_main_local_mode_requires_llm_api_key():
    """Local mode requires LLM_API_KEY."""
    env = {**_BASE_ENV, "MODE": "local"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(SystemExit):
            main()


def test_main_cloud_mode_requires_openhands_api_key():
    """Cloud mode requires OPENHANDS_API_KEY."""
    env = {**_BASE_ENV, "MODE": "cloud"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(SystemExit):
            main()


def test_main_cloud_mode_does_not_require_llm_api_key():
    """Cloud mode should NOT require LLM_API_KEY."""
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
        # Verify the call includes prompt but no LLM config
        call_kwargs = mock_cloud.call_args[1]
        assert "prompt" in call_kwargs
        assert "github_token" in call_kwargs


def test_main_local_mode_dispatches_to_local():
    """Local mode dispatches to _run_local_mode."""
    env = {**_BASE_ENV, "MODE": "local", "LLM_API_KEY": "key"}
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
    env = {**_BASE_ENV, "LLM_API_KEY": "key"}
    # Ensure MODE is not set
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
