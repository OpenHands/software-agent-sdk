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
    LLM_API_KEY: API key for the LLM (required)
    LLM_MODEL: Language model to use (default: anthropic/claude-sonnet-4-5-20250929)
    LLM_BASE_URL: Optional base URL for LLM API
    GITHUB_TOKEN: GitHub token for API access (required)
    PR_NUMBER: Pull request number (required)
    PR_TITLE: Pull request title (required)
    PR_BODY: Pull request body (optional)
    PR_BASE_BRANCH: Base branch name (required)
    PR_HEAD_BRANCH: Head branch name (required)
    REPO_NAME: Repository name in format owner/repo (required)
    REVIEW_STYLE: Review style ('standard' or 'roasted', default: 'standard')

For setup instructions, usage examples, and GitHub Actions integration,
see README.md in this directory.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from lmnr import Laminar

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


def main():
    """Run the PR review agent."""
    logger.info("Starting PR review process...")

    # Validate required environment variables
    required_vars = [
        "LLM_API_KEY",
        "GITHUB_TOKEN",
        "PR_NUMBER",
        "PR_TITLE",
        "PR_BASE_BRANCH",
        "PR_HEAD_BRANCH",
        "REPO_NAME",
    ]

    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        logger.error(f"Missing required environment variables: {missing_vars}")
        sys.exit(1)

    github_token = os.getenv("GITHUB_TOKEN")

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

    try:
        pr_diff = get_truncated_pr_diff()
        logger.info(f"Got PR diff with {len(pr_diff)} characters")

        # Get the HEAD commit SHA for inline comments
        commit_id = get_head_commit_sha()
        logger.info(f"HEAD commit SHA: {commit_id}")

        # Create the review prompt using the template
        # Include the skill trigger keyword to activate the appropriate skill
        skill_trigger = (
            "/codereview" if review_style == "standard" else "/codereview-roasted"
        )
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
        # This loads skills from https://github.com/OpenHands/skills including:
        # - /codereview: Standard code review skill
        # - /codereview-roasted: Linus Torvalds style brutally honest review
        agent_context = AgentContext(
            load_public_skills=True,
        )

        # Create agent with default tools and agent context
        # Note: agent_context must be passed at initialization since Agent is frozen
        agent = Agent(
            llm=llm,
            tools=get_default_tools(enable_browser=False),  # CLI mode - no browser
            agent_context=agent_context,
            system_prompt_kwargs={"cli_mode": True},
            condenser=get_default_condenser(
                llm=llm.model_copy(update={"usage_id": "condenser"})
            ),
        )

        # Create conversation with secrets for masking
        # These secrets will be masked in agent output to prevent accidental exposure
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
        # The agent will analyze the code and post inline review comments
        # directly to the PR using the GitHub API
        conversation.send_message(prompt)
        conversation.run()

        # The agent should have posted review comments via GitHub API
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

        # Capture and store trace ID for delayed evaluation
        # When the PR is merged/closed, we can use this trace_id to evaluate
        # how well the review comments were addressed.
        # Note: Laminar methods gracefully handle the uninitialized case by
        # returning None or early-returning, so no try/except needed.
        trace_id = Laminar.get_trace_id()
        if trace_id:
            # Set trace metadata for later retrieval and filtering
            Laminar.set_trace_metadata(
                {
                    "pr_number": pr_info["number"],
                    "repo_name": pr_info["repo_name"],
                    "workflow_phase": "review",
                    "review_style": review_style,
                }
            )

            # Store trace_id in file for GitHub artifact upload
            # This allows the evaluation workflow to link back to this trace
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

            # Ensure trace is flushed to Laminar before workflow ends
            Laminar.flush()
        else:
            logger.warning(
                "No Laminar trace ID found - observability may not be enabled"
            )

        logger.info("PR review completed successfully")

    except Exception as e:
        logger.error(f"PR review failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
