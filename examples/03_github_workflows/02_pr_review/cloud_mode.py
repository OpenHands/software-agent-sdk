"""Cloud Mode - Run PR review in OpenHands Cloud using OpenHandsCloudWorkspace.

This module provides the cloud implementation for PR review, which creates a
cloud sandbox and starts the review asynchronously. The workflow exits immediately
after starting the review, and users can track progress in the OpenHands Cloud UI.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from typing import Any

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation, get_logger
from openhands.tools.preset.default import get_default_agent
from openhands.workspace import OpenHandsCloudWorkspace


logger = get_logger(__name__)


# Prompt template for cloud mode - agent fetches the PR diff itself
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


def run_agent_review(
    prompt: str,  # noqa: ARG001 - unused, cloud mode uses its own prompt
    pr_info: dict[str, Any],
    commit_id: str,  # noqa: ARG001 - unused in cloud mode
    review_style: str,  # noqa: ARG001 - unused, skill_trigger is derived from it
) -> None:
    """Run PR review in OpenHands Cloud using OpenHandsCloudWorkspace.

    This creates a cloud sandbox, starts the review conversation, posts a
    tracking comment, and exits immediately. The sandbox continues running
    asynchronously with keep_alive=True.

    Note: Cloud mode uses its own prompt template (CLOUD_MODE_PROMPT) that
    instructs the agent to fetch the PR diff itself. The `prompt` parameter
    is ignored.

    Args:
        prompt: The formatted review prompt (ignored in cloud mode)
        pr_info: PR information dict with keys: number, title, body, repo_name,
                 base_branch, head_branch
        commit_id: The HEAD commit SHA (unused in cloud mode)
        review_style: Review style ('standard' or 'roasted')
    """
    cloud_api_key = os.getenv("OPENHANDS_CLOUD_API_KEY")
    if not cloud_api_key:
        raise ValueError(
            "OPENHANDS_CLOUD_API_KEY environment variable is required for cloud mode"
        )

    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        raise ValueError("GITHUB_TOKEN environment variable is required")

    cloud_api_url = os.getenv("OPENHANDS_CLOUD_API_URL", "https://app.all-hands.dev")

    # LLM_API_KEY is optional for cloud mode - the cloud uses user's configured LLM
    llm_api_key = os.getenv("LLM_API_KEY")
    llm_model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
    llm_base_url = os.getenv("LLM_BASE_URL")

    # Derive skill trigger from review style
    skill_trigger = (
        "/codereview" if review_style == "standard" else "/codereview-roasted"
    )

    # Create cloud-specific prompt
    cloud_prompt = CLOUD_MODE_PROMPT.format(
        skill_trigger=skill_trigger,
        repo_name=pr_info["repo_name"],
        pr_number=pr_info["number"],
        title=pr_info["title"],
        body=pr_info["body"] or "No description provided",
        base_branch=pr_info["base_branch"],
        head_branch=pr_info["head_branch"],
    )

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
        conversation.send_message(cloud_prompt)

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
        _post_github_comment(pr_info["repo_name"], pr_info["number"], comment_body)

        # Trigger the run with blocking=False so we exit immediately.
        # With keep_alive=True, the cloud sandbox continues running the review
        # asynchronously while this workflow exits.
        conversation.run(blocking=False)
        logger.info(f"Cloud review started (non-blocking): {conversation_url}")


def _post_github_comment(repo_name: str, pr_number: str, body: str) -> None:
    """Post a comment on a GitHub PR."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN environment variable is required")

    url = f"https://api.github.com/repos/{repo_name}/issues/{pr_number}/comments"

    import json

    data = json.dumps({"body": body}).encode("utf-8")

    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Accept", "application/vnd.github.v3+json")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response.read()
        logger.info(f"Posted comment to PR #{pr_number}")
    except urllib.error.HTTPError as e:
        details = (e.read() or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"GitHub API request failed: HTTP {e.code} {e.reason}. {details}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"GitHub API request failed: {e.reason}") from e
