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
from pathlib import Path

from openhands.sdk import LLM, AgentContext, Conversation, get_logger
from openhands.sdk.conversation import get_agent_final_response
from openhands.sdk.utils.github import sanitize_openhands_mentions
from openhands.tools.preset.default import get_default_agent


# Add the script directory to Python path so we can import prompt.py
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from prompt import PROMPT  # noqa: E402


logger = get_logger(__name__)


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
        agent = get_default_agent(
            llm=llm,
            cli_mode=True,
        )
        # Set agent_context on the agent
        agent.agent_context = agent_context

        # Create conversation
        conversation = Conversation(
            agent=agent,
            workspace=cwd,
        )

        logger.info("Starting PR review analysis...")
        logger.info(
            "Agent will analyze the PR using bash commands for full repository access"
        )
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
