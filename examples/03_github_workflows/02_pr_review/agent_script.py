#!/usr/bin/env python3
"""
Example: PR Review Agent

This script runs OpenHands agent to review a pull request and provide
fine-grained review comments. The agent has full repository access and uses
bash commands to analyze changes in context and post detailed review feedback.

This example demonstrates how to use skills for code review:
- `/codereview` - Standard code review skill
- `/codereview-roasted` - Linus Torvalds style brutally honest review

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

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from openhands.sdk import LLM, Agent, AgentContext, Conversation, get_logger
from openhands.sdk.conversation import get_agent_final_response
from openhands.sdk.git.utils import run_git_command
from openhands.sdk.utils.github import sanitize_openhands_mentions
from openhands.tools.preset.default import get_default_condenser, get_default_tools


# Add the script directory to Python path so we can import prompt.py
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from prompt import PROMPT  # noqa: E402


logger = get_logger(__name__)

# Maximum characters per file diff before truncation
MAX_DIFF_PER_FILE = 10000
# Maximum total diff size
MAX_TOTAL_DIFF = 100000


@dataclass
class FileDiff:
    """Represents a diff for a single file."""

    path: str
    diff_content: str
    is_truncated: bool = False
    original_size: int = 0


def get_changed_files(base_ref: str, repo_dir: Path) -> list[str]:
    """
    Get list of files changed between base_ref and HEAD.

    Args:
        base_ref: Git reference to compare against (e.g., 'origin/main')
        repo_dir: Path to the git repository

    Returns:
        List of file paths that have changes
    """
    try:
        output = run_git_command(
            ["git", "--no-pager", "diff", "--name-only", f"{base_ref}...HEAD"],
            repo_dir,
        )
        return [f.strip() for f in output.splitlines() if f.strip()]
    except Exception as e:
        logger.warning(f"Failed to get changed files: {e}")
        return []


def get_file_diff(base_ref: str, file_path: str, repo_dir: Path) -> FileDiff:
    """
    Get the diff for a single file.

    Args:
        base_ref: Git reference to compare against
        file_path: Path to the file (relative to repo root)
        repo_dir: Path to the git repository

    Returns:
        FileDiff object with the diff content
    """
    try:
        diff_content = run_git_command(
            ["git", "--no-pager", "diff", f"{base_ref}...HEAD", "--", file_path],
            repo_dir,
        )
        return FileDiff(path=file_path, diff_content=diff_content)
    except Exception as e:
        logger.warning(f"Failed to get diff for {file_path}: {e}")
        return FileDiff(path=file_path, diff_content=f"[Error getting diff: {e}]")


def truncate_file_diff(
    file_diff: FileDiff, max_size: int = MAX_DIFF_PER_FILE
) -> FileDiff:
    """
    Truncate a file diff if it exceeds the maximum size.

    Args:
        file_diff: The FileDiff to potentially truncate
        max_size: Maximum allowed size in characters

    Returns:
        FileDiff, potentially truncated with a note
    """
    original_size = len(file_diff.diff_content)
    if original_size <= max_size:
        return file_diff

    truncated_content = (
        file_diff.diff_content[:max_size]
        + f"\n\n... [{file_diff.path}: diff truncated, {original_size:,} chars "
        + f"total, showing first {max_size:,}] ...\n"
    )

    return FileDiff(
        path=file_diff.path,
        diff_content=truncated_content,
        is_truncated=True,
        original_size=original_size,
    )


def get_pr_diff(base_branch: str, repo_dir: Path | None = None) -> list[FileDiff]:
    """
    Get structured diff for all changed files in the PR.

    Args:
        base_branch: The base branch to compare against (e.g., 'main')
        repo_dir: Path to the repository (defaults to cwd)

    Returns:
        List of FileDiff objects for each changed file
    """
    if repo_dir is None:
        repo_dir = Path.cwd()

    # Fetch the base branch to ensure we have latest refs
    try:
        subprocess.run(
            ["git", "fetch", "origin", base_branch],
            cwd=repo_dir,
            capture_output=True,
            check=False,
        )
    except Exception as e:
        logger.warning(f"Failed to fetch origin/{base_branch}: {e}")

    # Determine the base reference
    base_ref = f"origin/{base_branch}"

    # Get list of changed files
    changed_files = get_changed_files(base_ref, repo_dir)
    if not changed_files:
        logger.info("No changed files found")
        return []

    logger.info(f"Found {len(changed_files)} changed file(s)")

    # Get diff for each file
    file_diffs = []
    for file_path in changed_files:
        file_diff = get_file_diff(base_ref, file_path, repo_dir)
        file_diffs.append(file_diff)

    return file_diffs


def format_pr_diff(
    file_diffs: list[FileDiff],
    max_per_file: int = MAX_DIFF_PER_FILE,
    max_total: int = MAX_TOTAL_DIFF,
) -> str:
    """
    Format file diffs into a single string with truncation.

    Args:
        file_diffs: List of FileDiff objects
        max_per_file: Maximum characters per file diff
        max_total: Maximum total characters

    Returns:
        Formatted diff string
    """
    if not file_diffs:
        return "[No changes found]"

    # Truncate individual file diffs
    truncated_diffs = [truncate_file_diff(fd, max_per_file) for fd in file_diffs]

    truncated_count = sum(1 for fd in truncated_diffs if fd.is_truncated)
    if truncated_count > 0:
        logger.info(f"Truncated {truncated_count} large file diff(s)")

    # Combine all diffs
    result = "\n".join(fd.diff_content for fd in truncated_diffs)

    # Enforce total size limit
    if len(result) > max_total:
        total_chars = len(result)
        result = (
            result[:max_total]
            + f"\n\n... [total diff truncated, {total_chars:,} chars total, "
            + f"showing first {max_total:,}] ..."
        )
        logger.info(f"Total diff truncated to {max_total} chars")

    return result


def get_truncated_pr_diff(base_branch: str) -> str:
    """
    Get the PR diff with large file diffs truncated.

    Args:
        base_branch: The base branch to compare against

    Returns:
        The truncated git diff output as a formatted string
    """
    file_diffs = get_pr_diff(base_branch)
    return format_pr_diff(file_diffs)


def post_review_comment(review_content: str) -> None:
    """
    Post a review comment to the PR using GitHub CLI.

    Args:
        review_content: The review content to post
    """
    # Sanitize @OpenHands mentions to prevent self-mention loops
    review_content = sanitize_openhands_mentions(review_content)

    logger.info("Posting review comment to GitHub...")
    pr_number = os.getenv("PR_NUMBER")
    repo_name = os.getenv("REPO_NAME")
    github_token = os.getenv("GITHUB_TOKEN")

    if not pr_number or not repo_name or not github_token:
        raise RuntimeError("Missing required environment variables for posting review")

    subprocess.run(
        [
            "gh",
            "pr",
            "review",
            pr_number,
            "--repo",
            repo_name,
            "--comment",
            "--body",
            review_content,
        ],
        check=True,
        env={**os.environ, "GH_TOKEN": github_token},
    )
    logger.info("Successfully posted review comment")


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
        # Get the PR diff upfront so the agent has it in the initial message
        base_branch = pr_info["base_branch"]
        logger.info(f"Getting git diff from origin/{base_branch}...")
        pr_diff = get_truncated_pr_diff(base_branch)
        logger.info(f"Got PR diff with {len(pr_diff)} characters")

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

        # Create conversation
        conversation = Conversation(
            agent=agent,
            workspace=cwd,
        )

        logger.info("Starting PR review analysis...")
        logger.info("Agent received the PR diff in the initial message")
        logger.info(f"Using skill trigger: {skill_trigger}")

        # Send the prompt and run the agent
        conversation.send_message(prompt)
        conversation.run()

        # Get the agent's response
        review_content = get_agent_final_response(conversation.state.events)
        if not review_content:
            raise RuntimeError("No review content generated by the agent")

        logger.info(f"Generated review with {len(review_content)} characters")

        # Post the review comment
        post_review_comment(review_content)

        logger.info("PR review completed successfully")

    except Exception as e:
        logger.error(f"PR review failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
