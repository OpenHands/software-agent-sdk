#!/usr/bin/env python3
"""
Example: PR Review Agent

This script runs OpenHands agent to review a pull request and provide
fine-grained review comments. The agent has full repository access and uses
bash commands to analyze changes in context and post detailed review feedback
directly via `gh` or the GitHub API.

This example demonstrates how to use skills for code review:
- `/codereview` - Standard code review skill
- `/codereview-roasted` - Linus Torvalds style brutally honest review

The agent posts inline review comments on specific lines of code using the
GitHub API, rather than posting one giant comment under the PR.

Designed for use with GitHub Actions workflows triggered by PR labels.

Environment Variables:
    MODE: Review mode ('sdk' or 'cloud', default: 'sdk')
        - 'sdk': Run the agent locally using the SDK (default)
        - 'cloud': Launch a review task in OpenHands Cloud and exit
    LLM_API_KEY: API key for the LLM (required for 'sdk' mode, ignored in 'cloud' mode)
    LLM_MODEL: Language model to use (required for 'sdk' mode, ignored in 'cloud' mode)
    LLM_BASE_URL: Optional base URL for LLM API (only used in 'sdk' mode)
    GITHUB_TOKEN: GitHub token for API access (required)
    PR_NUMBER: Pull request number (required)
    PR_TITLE: Pull request title (required)
    PR_BODY: Pull request body (optional)
    PR_BASE_BRANCH: Base branch name (required)
    PR_HEAD_BRANCH: Head branch name (required)
    REPO_NAME: Repository name in format owner/repo (required)
    REVIEW_STYLE: Review style ('standard' or 'roasted', default: 'standard')
    OPENHANDS_CLOUD_API_KEY: API key for OpenHands Cloud (required for 'cloud' mode)
    OPENHANDS_CLOUD_API_URL: OpenHands Cloud API URL (default: https://app.all-hands.dev)

Note: In 'cloud' mode, LLM_MODEL and LLM_BASE_URL are ignored. The reviewer bot
will use the model configured in your OpenHands Cloud account.

For setup instructions, usage examples, and GitHub Actions integration,
see README.md in this directory.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from openhands.sdk import LLM, Agent, AgentContext, Conversation, get_logger
from openhands.sdk.conversation import get_agent_final_response
from openhands.sdk.git.utils import run_git_command
from openhands.tools.preset.default import get_default_condenser, get_default_tools


# Add the script directory to Python path so we can import prompt.py
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from prompt import PROMPT  # noqa: E402


logger = get_logger(__name__)

# Maximum total diff size
MAX_TOTAL_DIFF = 100000

# Template for cloud mode PR comment
CLOUD_REVIEW_COMMENT_TEMPLATE = """\
🤖 **OpenHands PR Review Started**

A code review has been initiated in OpenHands Cloud.

📍 **Track progress here:** [{conversation_url}]({conversation_url})

The review will analyze the changes and post inline comments \
directly on this PR when complete."""


def _get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} environment variable is required")
    return value


def get_pr_diff_via_github_api(pr_number: str) -> str:
    """Fetch the PR diff exactly as GitHub renders it.

    Uses the GitHub REST API "Get a pull request" endpoint with an `Accept`
    header requesting diff output.

    This avoids depending on local git refs (often stale/missing in
    `pull_request_target` checkouts).
    """

    repo = _get_required_env("REPO_NAME")
    token = _get_required_env("GITHUB_TOKEN")

    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github.v3.diff")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
    except urllib.error.HTTPError as e:
        details = (e.read() or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"GitHub diff API request failed: HTTP {e.code} {e.reason}. {details}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"GitHub diff API request failed: {e.reason}") from e

    return data.decode("utf-8", errors="replace")


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
    """Post a comment on a GitHub PR.

    Args:
        repo_name: Repository name in format owner/repo
        pr_number: Pull request number
        body: Comment body text
    """
    token = _get_required_env("GITHUB_TOKEN")
    url = f"https://api.github.com/repos/{repo_name}/issues/{pr_number}/comments"

    data = json.dumps({"body": body}).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Accept", "application/vnd.github.v3+json")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            logger.info(f"Posted comment to PR #{pr_number}: {response.status}")
    except urllib.error.HTTPError as e:
        details = (e.read() or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"GitHub comment API request failed: HTTP {e.code} {e.reason}. {details}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"GitHub comment API request failed: {e.reason}") from e


def _prepare_review_context(pr_info: dict, review_style: str) -> tuple[str, str]:
    """Prepare the review context including diff and prompt.

    This is shared setup code used by both cloud and SDK modes.

    Args:
        pr_info: Dictionary containing PR information
        review_style: Review style ('standard' or 'roasted')

    Returns:
        Tuple of (prompt, skill_trigger)
    """
    pr_diff = get_truncated_pr_diff()
    logger.info(f"Got PR diff with {len(pr_diff)} characters")

    # Get the HEAD commit SHA for inline comments
    commit_id = get_head_commit_sha()
    logger.info(f"HEAD commit SHA: {commit_id}")

    # Determine skill trigger based on review style
    skill_trigger = (
        "/codereview" if review_style == "standard" else "/codereview-roasted"
    )

    # Create the review prompt using the template
    prompt = PROMPT.format(
        title=pr_info.get("title", "N/A"),
        body=pr_info.get("body", "No description provided"),
        repo_name=pr_info.get("repo_name", "N/A"),
        base_branch=pr_info.get("base_branch", "main"),
        head_branch=pr_info.get("head_branch", "N/A"),
        pr_number=pr_info.get("number", "N/A"),
        commit_id=commit_id,
        skill_trigger=skill_trigger,
        diff=pr_diff,
    )

    return prompt, skill_trigger


def _start_cloud_conversation(
    cloud_api_url: str,
    cloud_api_key: str,
    prompt: str,
    github_token: str | None = None,
) -> str:
    """Start a conversation in OpenHands Cloud.

    Args:
        cloud_api_url: OpenHands Cloud API URL
        cloud_api_key: OpenHands Cloud API key
        prompt: The initial prompt for the conversation
        github_token: Optional GitHub token to pass as a secret

    Returns:
        The conversation ID
    """
    url = f"{cloud_api_url}/api/conversations"

    # Build the request payload
    # Note: The OpenHands Cloud API does not accept secrets directly.
    # Instead, it uses the user's connected GitHub account for repository access.
    # The github_token is available in the agent's environment for API calls.
    payload: dict = {
        "initial_user_msg": prompt,
    }

    if github_token:
        logger.info(
            "GitHub token provided but not sent to Cloud API. "
            "OpenHands Cloud uses your connected GitHub account for repository access."
        )

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Authorization", f"Bearer {cloud_api_key}")
    request.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
            conversation_id = result.get("conversation_id") or result.get("id")
            if not conversation_id:
                raise RuntimeError(
                    f"Cloud API response missing conversation_id: {result}"
                )
            logger.info(f"Created cloud conversation: {conversation_id}")
            return conversation_id
    except urllib.error.HTTPError as e:
        details = (e.read() or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"Cloud API request failed: HTTP {e.code} {e.reason}. {details}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cloud API request failed: {e.reason}") from e


def run_cloud_mode(pr_info: dict, prompt: str) -> None:
    """Run the PR review in OpenHands Cloud mode.

    This mode:
    1. Creates an OpenHands Cloud conversation
    2. Sends the review prompt to the cloud
    3. Posts a comment on the PR with the cloud conversation URL
    4. Exits without monitoring the conversation

    Note: In cloud mode, LLM_MODEL and LLM_BASE_URL are ignored. The reviewer
    bot will use the model configured in your OpenHands Cloud account.

    Args:
        pr_info: Dictionary containing PR information (repo_name, number)
        prompt: The review prompt to send to the cloud
    """
    # Get cloud-specific configuration (already validated in main())
    cloud_api_key = _get_required_env("OPENHANDS_CLOUD_API_KEY")
    cloud_api_url = os.getenv("OPENHANDS_CLOUD_API_URL", "https://app.all-hands.dev")

    logger.info(f"Using OpenHands Cloud API: {cloud_api_url}")
    logger.info(
        "Note: LLM_MODEL and LLM_BASE_URL are ignored in cloud mode. "
        "The model configured in your OpenHands Cloud account will be used."
    )

    try:
        logger.info("Starting OpenHands Cloud conversation...")

        # Create conversation via cloud API
        github_token = os.getenv("GITHUB_TOKEN")
        conversation_id = _start_cloud_conversation(
            cloud_api_url=cloud_api_url,
            cloud_api_key=cloud_api_key,
            prompt=prompt,
            github_token=github_token,
        )

        # Build the cloud conversation URL
        conversation_url = f"{cloud_api_url}/conversations/{conversation_id}"
        logger.info(f"Cloud conversation URL: {conversation_url}")
        logger.info("Review task started in OpenHands Cloud")

        # Post a comment on the PR with the cloud URL
        comment_body = CLOUD_REVIEW_COMMENT_TEMPLATE.format(
            conversation_url=conversation_url
        )

        post_github_comment(
            repo_name=pr_info["repo_name"],
            pr_number=pr_info["number"],
            body=comment_body,
        )

        logger.info("Posted cloud review notification comment to PR")
        logger.info("Exiting - review will continue in OpenHands Cloud")

    except Exception as e:
        logger.error(f"Cloud mode PR review failed: {e}")
        sys.exit(1)


def run_sdk_mode(prompt: str, skill_trigger: str) -> None:
    """Run the PR review in SDK mode (local execution).

    This is the original behavior - runs the agent locally and monitors
    until completion.

    Args:
        prompt: The review prompt to send to the agent
        skill_trigger: The skill trigger to use (e.g., '/codereview')
    """
    github_token = os.getenv("GITHUB_TOKEN")

    try:
        # Configure LLM
        api_key = os.getenv("LLM_API_KEY")
        model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
        base_url = os.getenv("LLM_BASE_URL")

        llm_config = {
            "model": model,
            "api_key": api_key,
            "usage_id": "pr_review_agent",
            "drop_params": True,
        }

        if base_url:
            llm_config["base_url"] = base_url

        llm = LLM(**llm_config)

        # Get the current working directory as workspace
        cwd = os.getcwd()

        # Create AgentContext with public skills enabled
        agent_context = AgentContext(load_public_skills=True)

        # Create agent with default tools and agent context
        agent = Agent(
            llm=llm,
            tools=get_default_tools(enable_browser=False),
            agent_context=agent_context,
            system_prompt_kwargs={"cli_mode": True},
            condenser=get_default_condenser(
                llm=llm.model_copy(update={"usage_id": "condenser"})
            ),
        )

        # Create conversation with secrets for masking
        secrets = {}
        if api_key:
            secrets["LLM_API_KEY"] = api_key
        if github_token:
            secrets["GITHUB_TOKEN"] = github_token

        conversation = Conversation(
            agent=agent,
            workspace=cwd,
            secrets=secrets,
        )

        logger.info("Starting PR review analysis...")
        logger.info("Agent received the PR diff in the initial message")
        logger.info(f"Using skill trigger: {skill_trigger}")
        logger.info("Agent will post inline review comments directly via GitHub API")

        # Send the prompt and run the agent
        conversation.send_message(prompt)
        conversation.run()

        # Log the final response for debugging purposes
        review_content = get_agent_final_response(conversation.state.events)
        if review_content:
            logger.info(f"Agent final response: {len(review_content)} characters")

        # Print cost information for CI output
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

        logger.info("PR review completed successfully")

    except Exception as e:
        logger.error(f"PR review failed: {e}")
        sys.exit(1)


def main():
    """Run the PR review agent."""
    logger.info("Starting PR review process...")

    # Get mode
    mode = os.getenv("MODE", "sdk").lower()
    if mode not in ("sdk", "cloud"):
        logger.warning(f"Unknown MODE '{mode}', using 'sdk'")
        mode = "sdk"

    logger.info(f"Mode: {mode}")

    # Validate required environment variables based on mode
    base_required_vars = [
        "GITHUB_TOKEN",
        "PR_NUMBER",
        "PR_TITLE",
        "PR_BASE_BRANCH",
        "PR_HEAD_BRANCH",
        "REPO_NAME",
    ]

    if mode == "sdk":
        # SDK mode requires LLM_API_KEY for local LLM access
        required_vars = base_required_vars + ["LLM_API_KEY"]
    else:  # cloud mode
        # Cloud mode only requires OPENHANDS_CLOUD_API_KEY
        # LLM_MODEL and LLM_BASE_URL are ignored - cloud uses its own model
        required_vars = base_required_vars + ["OPENHANDS_CLOUD_API_KEY"]

    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        logger.error(f"Missing required environment variables: {missing_vars}")
        sys.exit(1)

    # Get PR information
    pr_info = {
        "number": os.getenv("PR_NUMBER"),
        "title": os.getenv("PR_TITLE"),
        "body": os.getenv("PR_BODY", ""),
        "repo_name": os.getenv("REPO_NAME"),
        "base_branch": os.getenv("PR_BASE_BRANCH"),
        "head_branch": os.getenv("PR_HEAD_BRANCH"),
    }

    # Get review style - default to standard
    review_style = os.getenv("REVIEW_STYLE", "standard").lower()
    if review_style not in ("standard", "roasted"):
        logger.warning(f"Unknown REVIEW_STYLE '{review_style}', using 'standard'")
        review_style = "standard"

    logger.info(f"Reviewing PR #{pr_info['number']}: {pr_info['title']}")
    logger.info(f"Review style: {review_style}")

    # Prepare review context (shared by both modes)
    prompt, skill_trigger = _prepare_review_context(pr_info, review_style)

    # Run the appropriate mode
    if mode == "cloud":
        run_cloud_mode(pr_info, prompt)
    else:
        run_sdk_mode(prompt, skill_trigger)


if __name__ == "__main__":
    main()
