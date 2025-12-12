#!/usr/bin/env python3
"""
Example: Changelog Generator

This script runs OpenHands agent to generate a changelog from git commit history.
The agent analyzes commits in a specified range and creates/updates a
CHANGELOG.md file following the Keep a Changelog format.

Designed for use with GitHub Actions workflows (scheduled or manual trigger)
or local execution.

Environment Variables:
    LLM_API_KEY: API key for the LLM (required)
    LLM_MODEL: Language model to use (default:
        openhands/claude-sonnet-4-5-20250929)
    LLM_BASE_URL: Optional base URL for LLM API
    START_REF: Start reference - date (YYYY-MM-DD), commit SHA, or tag
        (default: 7 days ago)
    END_REF: End reference - date (YYYY-MM-DD), commit SHA, or tag
        (default: today)
    START_DATE: Alias for START_REF (backward compatibility)
    END_DATE: Alias for END_REF (backward compatibility)
    REPO_NAME: Repository name in format owner/repo (optional, auto-detected from git)
    CREATE_PR: Whether to create a PR with changes (default: false)
    GITHUB_TOKEN: GitHub token for PR creation (required if CREATE_PR=true)
    CHANGELOG_PROMPT_FILE: Optional path to a file to override the built-in prompt
    CHANGELOG_PROMPT_APPEND: Optional text to append to the final prompt

For setup instructions, usage examples, and GitHub Actions integration,
see README.md in this directory.
"""

import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path


# Add the script directory to Python path so we can import prompt.py
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from prompt import PROMPT  # noqa: E402

from openhands.sdk import LLM, Conversation, get_logger  # noqa: E402
from openhands.tools.preset.default import get_default_agent  # noqa: E402


logger = get_logger(__name__)


def detect_ref_type(value: str) -> str:
    """
    Auto-detect the type of git reference.

    Args:
        value: The reference value to detect

    Returns:
        One of: 'date', 'commit', 'tag'
    """
    # Date: YYYY-MM-DD pattern
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return "date"
    # Commit: 7-40 hex characters (lowercase)
    if re.match(r"^[0-9a-f]{7,40}$", value.lower()):
        return "commit"
    # Tag: everything else (v1.0.0, 1.0.0a1, release-1.0, etc.)
    return "tag"


def get_range() -> tuple[str, str, str, str]:
    """
    Get the range for changelog generation.

    Supports dates (YYYY-MM-DD), commit SHAs, or tags.
    Falls back to START_DATE/END_DATE for backward compatibility.

    Returns:
        Tuple of (start_ref, end_ref, start_type, end_type)
    """
    # Check new env vars first, fall back to old ones for backward compat
    end_ref = os.getenv("END_REF") or os.getenv("END_DATE")
    if not end_ref:
        end_ref = datetime.now().strftime("%Y-%m-%d")

    start_ref = os.getenv("START_REF") or os.getenv("START_DATE")
    if not start_ref:
        start_ref = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    return start_ref, end_ref, detect_ref_type(start_ref), detect_ref_type(end_ref)


def get_repo_name() -> str:
    """
    Get the repository name from environment or git remote.

    Returns:
        Repository name in format owner/repo
    """
    repo_name = os.getenv("REPO_NAME")
    if repo_name:
        return repo_name

    # Try to extract from git remote
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
        remote_url = result.stdout.strip()

        # Parse GitHub URL
        if "github.com" in remote_url:
            if remote_url.endswith(".git"):
                remote_url = remote_url[:-4]
            parts = remote_url.split("github.com")[-1].strip("/:")
            return parts

        return "unknown/repo"
    except Exception:
        return "unknown/repo"


def create_pr_with_changelog() -> None:
    """
    Create a pull request with the changelog changes using GitHub CLI.
    """
    logger.info("Creating PR with changelog changes...")

    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        logger.warning("GITHUB_TOKEN not set, skipping PR creation")
        return

    repo_name = get_repo_name()
    start_ref, end_ref, _, _ = get_range()

    # Create a branch for the changelog
    branch_name = f"changelog-{end_ref.replace('/', '-')}"

    try:
        # Check if there are changes to commit
        result = subprocess.run(
            ["git", "status", "--porcelain", "CHANGELOG.md"],
            capture_output=True,
            text=True,
        )

        if not result.stdout.strip():
            logger.info("No changes to CHANGELOG.md, skipping PR creation")
            return

        # Create and checkout new branch
        subprocess.run(["git", "checkout", "-b", branch_name], check=True)

        # Add and commit changes
        subprocess.run(["git", "add", "CHANGELOG.md"], check=True)
        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                f"docs: update changelog for {start_ref} to {end_ref}",
            ],
            check=True,
        )

        # Push branch
        subprocess.run(["git", "push", "origin", branch_name], check=True)

        # Create PR using gh CLI
        pr_body = (
            f"Automated changelog update for changes from "
            f"{start_ref} to {end_ref}.\n\n"
            f"Generated by OpenHands Agent SDK."
        )

        subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                repo_name,
                "--title",
                f"Update changelog ({start_ref} to {end_ref})",
                "--body",
                pr_body,
                "--head",
                branch_name,
            ],
            check=True,
            env={**os.environ, "GH_TOKEN": github_token},
        )

        logger.info(f"Successfully created PR for branch {branch_name}")

    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create PR: {e}")
        # Don't fail the whole script if PR creation fails
    except Exception as e:
        logger.error(f"Unexpected error creating PR: {e}")


def main():
    """Run the changelog generation agent."""
    logger.info("Starting changelog generation process...")

    # Validate required environment variables
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        logger.error("LLM_API_KEY environment variable is not set.")
        sys.exit(1)

    # Get range and repo info
    start_ref, end_ref, start_type, end_type = get_range()
    repo_name = get_repo_name()

    logger.info(f"Generating changelog for {start_ref} to {end_ref}")
    logger.info(f"Reference types: {start_type} -> {end_type}")
    logger.info(f"Repository: {repo_name}")

    try:
        # Create the changelog prompt (allow env override)
        raw_prompt = PROMPT
        override_path = os.getenv("CHANGELOG_PROMPT_FILE")
        if override_path and os.path.isfile(override_path):
            try:
                with open(override_path, encoding="utf-8") as f:
                    raw_prompt = f.read()
            except Exception:
                pass
        prompt = raw_prompt.format(
            start_ref=start_ref,
            end_ref=end_ref,
            start_type=start_type,
            end_type=end_type,
            repo_name=repo_name,
        )
        extra = os.getenv("CHANGELOG_PROMPT_APPEND")
        if extra:
            prompt = f"{prompt}\n{extra}"

        # Configure LLM
        model = os.getenv("LLM_MODEL", "openhands/claude-sonnet-4-5-20250929")
        base_url = os.getenv("LLM_BASE_URL")

        llm_config = {
            "model": model,
            "api_key": api_key,
            "usage_id": "changelog_generator",
            "drop_params": True,
        }

        if base_url:
            llm_config["base_url"] = base_url

        llm = LLM(**llm_config)

        # Get the current working directory as workspace
        cwd = os.getcwd()

        # Create agent with default tools
        agent = get_default_agent(
            llm=llm,
            cli_mode=True,
        )

        # Create conversation
        conversation = Conversation(
            agent=agent,
            workspace=cwd,
        )

        logger.info("Starting changelog generation analysis...")
        logger.info("Agent will analyze git history and create/update CHANGELOG.md")

        # Send the prompt and run the agent
        conversation.send_message(prompt)
        conversation.run()

        logger.info("Changelog generation completed successfully")

        # Check if CHANGELOG.md was created/updated
        if os.path.exists("CHANGELOG.md"):
            logger.info("CHANGELOG.md file created/updated")

            # Optionally create a PR
            if os.getenv("CREATE_PR", "false").lower() == "true":
                create_pr_with_changelog()
        else:
            logger.warning("CHANGELOG.md file was not created")

    except Exception as e:
        logger.error(f"Changelog generation failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
