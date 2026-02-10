#!/usr/bin/env python3
"""PR Review Agent - Automated code review using OpenHands.

Supports two modes:
- 'sdk': Run locally using the SDK (default)
- 'cloud': Run in OpenHands Cloud using OpenHandsCloudWorkspace

See README.md for setup instructions and usage examples.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, TypedDict

import requests
from lmnr import Laminar
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext, Conversation, get_logger
from openhands.sdk.conversation import get_agent_final_response
from openhands.sdk.conversation.base import BaseConversation
from openhands.sdk.git.utils import run_git_command
from openhands.tools.preset.default import (
    get_default_agent,
    get_default_condenser,
    get_default_tools,
)
from openhands.workspace import OpenHandsCloudWorkspace


# Add the script directory to Python path so we can import prompt.py
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from prompt import PROMPT  # noqa: E402


logger = get_logger(__name__)


class PRInfo(TypedDict):
    """Pull request information."""

    number: str
    title: str
    body: str
    repo_name: str
    base_branch: str
    head_branch: str


# Maximum total diff size
MAX_TOTAL_DIFF = 100000


def _get_required_env(name: str) -> str:
    """Get a required environment variable or raise ValueError."""
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} environment variable is required")
    return value


def get_pr_diff_via_github_api(pr_number: str) -> str:
    """Fetch the PR diff exactly as GitHub renders it."""
    repo = _get_required_env("REPO_NAME")
    token = _get_required_env("GITHUB_TOKEN")

    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
    headers = {
        "Accept": "application/vnd.github.v3.diff",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    return response.text


def truncate_text(diff_text: str, max_total: int = MAX_TOTAL_DIFF) -> str:
    if len(diff_text) <= max_total:
        return diff_text

    total_chars = len(diff_text)
    return (
        diff_text[:max_total]
        + f"\n\n... [total diff truncated, {total_chars:,} chars total, "
        + f"showing first {max_total:,}] ..."
    )


def get_truncated_pr_diff() -> str:
    """Get the PR diff with truncation.

    This uses GitHub as the source of truth so the review matches the PR's
    "Files changed" view.
    """

    pr_number = _get_required_env("PR_NUMBER")
    diff_text = get_pr_diff_via_github_api(pr_number)
    return truncate_text(diff_text)


def get_head_commit_sha(repo_dir: Path | None = None) -> str:
    """
    Get the SHA of the HEAD commit.

    Args:
        repo_dir: Path to the repository (defaults to cwd)

    Returns:
        The commit SHA
    """
    if repo_dir is None:
        repo_dir = Path.cwd()

    return run_git_command(["git", "rev-parse", "HEAD"], repo_dir).strip()


def post_github_comment(repo_name: str, pr_number: str, body: str) -> None:
    """Post a comment on a GitHub PR."""
    token = _get_required_env("GITHUB_TOKEN")
    url = f"https://api.github.com/repos/{repo_name}/issues/{pr_number}/comments"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    response = requests.post(url, headers=headers, json={"body": body}, timeout=60)
    response.raise_for_status()
    logger.info(f"Posted comment to PR #{pr_number}")


# Prompt template for cloud mode - agent fetches the PR diff itself
# Note: GITHUB_TOKEN is automatically available in OpenHands Cloud environments
CLOUD_MODE_PROMPT = """{skill_trigger}
/github-pr-review

Review the PR and identify issues that need to be addressed.

## Pull Request Information
- **Repository**: {repo_name}
- **PR Number**: {pr_number}
- **Title**: {title}
- **Description**: {body}
- **Base Branch**: {base_branch}
- **Head Branch**: {head_branch}

## Instructions

1. First, clone the repository and fetch the PR diff:
   ```bash
   gh pr diff {pr_number} --repo {repo_name}
   ```

2. Analyze the changes thoroughly

3. Post your review using the GitHub API (GITHUB_TOKEN is already available)

IMPORTANT: When you have completed the code review, you MUST post a summary comment
on the PR. You can use the `gh` CLI:

```bash
gh pr comment {pr_number} --repo {repo_name} --body "## Code Review Complete

<your review summary here>"
```

Replace `<your review summary here>` with a brief summary of your review findings.
"""


def run_cloud_mode(pr_info: PRInfo, skill_trigger: str) -> None:
    """Run PR review in OpenHands Cloud using OpenHandsCloudWorkspace.

    This creates a cloud sandbox, starts the review conversation, posts a
    tracking comment, and exits immediately. The sandbox continues running
    asynchronously with keep_alive=True.

    Cloud mode uses the LLM configured in the user's OpenHands Cloud account,
    so LLM_API_KEY is optional. If provided, it will be passed to the agent.
    """
    prompt = CLOUD_MODE_PROMPT.format(
        skill_trigger=skill_trigger,
        repo_name=pr_info["repo_name"],
        pr_number=pr_info["number"],
        title=pr_info["title"],
        body=pr_info["body"] or "No description provided",
        base_branch=pr_info["base_branch"],
        head_branch=pr_info["head_branch"],
    )

    cloud_api_key = _get_required_env("OPENHANDS_CLOUD_API_KEY")
    cloud_api_url = os.getenv("OPENHANDS_CLOUD_API_URL", "https://app.all-hands.dev")

    # LLM_API_KEY is optional for cloud mode - the cloud uses user's configured LLM
    llm_api_key = os.getenv("LLM_API_KEY")
    llm_model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
    llm_base_url = os.getenv("LLM_BASE_URL")

    logger.info(f"Using OpenHands Cloud API: {cloud_api_url}")
    logger.info(f"Using skill trigger: {skill_trigger}")

    # Create LLM configuration - api_key is optional for cloud mode
    llm = LLM(
        usage_id="pr_review_agent",
        model=llm_model,
        api_key=SecretStr(llm_api_key) if llm_api_key else None,
        base_url=llm_base_url or None,
    )

    # Create cloud workspace with keep_alive=True so the sandbox continues
    # running after we exit
    with OpenHandsCloudWorkspace(
        cloud_api_url=cloud_api_url,
        cloud_api_key=cloud_api_key,
        keep_alive=True,
    ) as workspace:
        # Create agent with default tools
        agent = get_default_agent(llm=llm, cli_mode=True)

        # Get GitHub token for the conversation secrets
        github_token = _get_required_env("GITHUB_TOKEN")

        # Build secrets dict - only include LLM_API_KEY if provided
        secrets: dict[str, str] = {"GITHUB_TOKEN": github_token}
        if llm_api_key:
            secrets["LLM_API_KEY"] = llm_api_key

        # Create conversation
        conversation = Conversation(
            agent=agent,
            workspace=workspace,
            secrets=secrets,
        )

        # Send the initial message
        conversation.send_message(prompt)

        # Get conversation ID and construct URL
        conversation_id = str(conversation.id)
        conversation_url = f"{cloud_api_url}/conversations/{conversation_id}"

        logger.info(f"Cloud conversation started: {conversation_id}")

        # Post comment with tracking URL
        comment_body = (
            f"🤖 **OpenHands PR Review Started**\n\n"
            f"The code review is running in OpenHands Cloud.\n\n"
            f"📍 **Track progress:** [{conversation_url}]({conversation_url})\n\n"
            f"The agent will post review comments when the analysis is complete."
        )
        post_github_comment(pr_info["repo_name"], pr_info["number"], comment_body)

        # Trigger the run with blocking=False so we exit immediately.
        # With keep_alive=True, the cloud sandbox continues running the review
        # asynchronously while this workflow exits.
        conversation.run(blocking=False)
        logger.info(f"Cloud review started (non-blocking): {conversation_url}")


def run_sdk_mode(pr_info: PRInfo, skill_trigger: str, review_style: str) -> None:
    """Run PR review locally using the SDK (blocking)."""
    pr_diff = get_truncated_pr_diff()
    logger.info(f"Got PR diff with {len(pr_diff)} characters")

    commit_id = get_head_commit_sha()
    logger.info(f"HEAD commit SHA: {commit_id}")

    prompt = PROMPT.format(
        title=pr_info["title"],
        body=pr_info["body"] or "No description provided",
        repo_name=pr_info["repo_name"],
        base_branch=pr_info["base_branch"],
        head_branch=pr_info["head_branch"],
        pr_number=pr_info["number"],
        commit_id=commit_id,
        skill_trigger=skill_trigger,
        diff=pr_diff,
    )

    api_key = _get_required_env("LLM_API_KEY")
    github_token = _get_required_env("GITHUB_TOKEN")
    model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
    base_url = os.getenv("LLM_BASE_URL")

    llm_config: dict[str, Any] = {
        "model": model,
        "api_key": api_key,
        "usage_id": "pr_review_agent",
        "drop_params": True,
    }
    if base_url:
        llm_config["base_url"] = base_url
    llm = LLM(**llm_config)

    agent = Agent(
        llm=llm,
        tools=get_default_tools(enable_browser=False),
        agent_context=AgentContext(load_public_skills=True),
        system_prompt_kwargs={"cli_mode": True},
        condenser=get_default_condenser(
            llm=llm.model_copy(update={"usage_id": "condenser"})
        ),
    )

    conversation = Conversation(
        agent=agent,
        workspace=os.getcwd(),
        secrets={"LLM_API_KEY": api_key, "GITHUB_TOKEN": github_token},
    )

    logger.info("Starting PR review analysis...")
    logger.info(f"Using skill trigger: {skill_trigger}")

    conversation.send_message(prompt)
    conversation.run()

    review_content = get_agent_final_response(conversation.state.events)
    if review_content:
        logger.info(f"Agent final response: {len(review_content)} characters")

    _print_cost_summary(conversation)
    _save_laminar_trace(pr_info, commit_id, review_style)

    logger.info("PR review completed successfully")


def _print_cost_summary(conversation: BaseConversation) -> None:
    """Print cost information for CI output."""
    metrics = conversation.conversation_stats.get_combined_metrics()
    print("\n=== PR Review Cost Summary ===")
    print(f"Total Cost: ${metrics.accumulated_cost:.6f}")
    if metrics.accumulated_token_usage:
        token_usage = metrics.accumulated_token_usage
        print(f"Prompt Tokens: {token_usage.prompt_tokens}")
        print(f"Completion Tokens: {token_usage.completion_tokens}")
        if token_usage.cache_read_tokens > 0:
            print(f"Cache Read Tokens: {token_usage.cache_read_tokens}")
        if token_usage.cache_write_tokens > 0:
            print(f"Cache Write Tokens: {token_usage.cache_write_tokens}")


def _save_laminar_trace(pr_info: PRInfo, commit_id: str, review_style: str) -> None:
    """Save Laminar trace info for delayed evaluation."""
    trace_id = Laminar.get_trace_id()
    if not trace_id:
        logger.warning("No Laminar trace ID found - observability may not be enabled")
        return

    Laminar.set_trace_metadata(
        {
            "pr_number": pr_info["number"],
            "repo_name": pr_info["repo_name"],
            "workflow_phase": "review",
            "review_style": review_style,
        }
    )

    trace_data = {
        "trace_id": str(trace_id),
        "pr_number": pr_info["number"],
        "repo_name": pr_info["repo_name"],
        "commit_id": commit_id,
        "review_style": review_style,
    }
    with open("laminar_trace_info.json", "w") as f:
        json.dump(trace_data, f, indent=2)

    logger.info(f"Laminar trace ID: {trace_id}")
    print("\n=== Laminar Trace ===")
    print(f"Trace ID: {trace_id}")

    Laminar.flush()


def _get_required_vars_for_mode(mode: str) -> list[str]:
    """Get required environment variables for the given mode."""
    common_vars = [
        "GITHUB_TOKEN",
        "PR_NUMBER",
        "PR_TITLE",
        "PR_BASE_BRANCH",
        "PR_HEAD_BRANCH",
        "REPO_NAME",
    ]
    if mode == "cloud":
        # Cloud mode only requires OPENHANDS_CLOUD_API_KEY
        # LLM is configured in the user's OpenHands Cloud account
        return ["OPENHANDS_CLOUD_API_KEY"] + common_vars
    # SDK mode requires LLM_API_KEY for local LLM execution
    return ["LLM_API_KEY"] + common_vars


def _get_pr_info() -> PRInfo:
    """Get PR information from environment variables."""
    return PRInfo(
        number=os.getenv("PR_NUMBER", ""),
        title=os.getenv("PR_TITLE", ""),
        body=os.getenv("PR_BODY", ""),
        repo_name=os.getenv("REPO_NAME", ""),
        base_branch=os.getenv("PR_BASE_BRANCH", ""),
        head_branch=os.getenv("PR_HEAD_BRANCH", ""),
    )


def main() -> None:
    """Run the PR review agent."""
    logger.info("Starting PR review process...")

    mode = os.getenv("MODE", "sdk").lower()
    if mode not in ("sdk", "cloud"):
        logger.warning(f"Unknown MODE '{mode}', using 'sdk'")
        mode = "sdk"
    logger.info(f"Mode: {mode}")

    required_vars = _get_required_vars_for_mode(mode)
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        logger.error(f"Missing required environment variables: {missing_vars}")
        sys.exit(1)

    pr_info = _get_pr_info()
    logger.info(f"Reviewing PR #{pr_info['number']}: {pr_info['title']}")

    review_style = os.getenv("REVIEW_STYLE", "standard").lower()
    if review_style not in ("standard", "roasted"):
        logger.warning(f"Unknown REVIEW_STYLE '{review_style}', using 'standard'")
        review_style = "standard"
    logger.info(f"Review style: {review_style}")

    skill_trigger = (
        "/codereview" if review_style == "standard" else "/codereview-roasted"
    )

    try:
        if mode == "cloud":
            run_cloud_mode(pr_info, skill_trigger)
        else:
            run_sdk_mode(pr_info, skill_trigger, review_style)
    except Exception as e:
        logger.error(f"PR review failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
