#!/usr/bin/env python3
"""
Example: SRE Error Debugger

This script runs OpenHands agent to analyze test failures and debug errors.
The agent examines test output, investigates the codebase, and generates
an error analysis report with root causes and suggested fixes.

Designed for use with GitHub Actions workflows (trigger on test failures)
or local execution.

Environment Variables:
    LLM_API_KEY: API key for the LLM (required)
    LLM_MODEL: Language model to use (default: openhands/claude-sonnet-4-5-20250929)
    LLM_BASE_URL: Optional base URL for LLM API
    TEST_PATH: Path to tests to run (default: tests/)
    TEST_OUTPUT_FILE: Path to pre-existing test output file (optional)
    REPO_NAME: Repository name in format owner/repo (optional, auto-detected from git)
    CREATE_PR: Whether to create a PR with fixes (default: false)
    GITHUB_TOKEN: GitHub token for PR creation (required if CREATE_PR=true)

For setup instructions, usage examples, and GitHub Actions integration,
see README.md in this directory.
"""

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


# Add the script directory to Python path so we can import prompt.py
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from prompt import PROMPT  # noqa: E402

from openhands.sdk import LLM, Conversation, get_logger  # noqa: E402
from openhands.tools.preset.default import get_default_agent  # noqa: E402


logger = get_logger(__name__)


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


def run_tests_and_capture_output(test_path: str) -> str:
    """
    Run pytest and capture test output.

    Args:
        test_path: Path to tests to run

    Returns:
        Combined stdout and stderr from pytest
    """
    logger.info(f"Running tests in {test_path}...")

    try:
        result = subprocess.run(
            ["pytest", test_path, "-v", "--tb=short", "--no-header"],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )

        output = result.stdout + "\n" + result.stderr
        logger.info(f"Tests completed with exit code {result.returncode}")

        return output
    except subprocess.TimeoutExpired:
        logger.error("Test execution timed out after 5 minutes")
        return "ERROR: Test execution timed out"
    except Exception as e:
        logger.error(f"Failed to run tests: {e}")
        return f"ERROR: Failed to run tests: {e}"


def load_test_output(file_path: str) -> str:
    """
    Load test output from a file.

    Args:
        file_path: Path to the test output file

    Returns:
        Test output content
    """
    try:
        with open(file_path) as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to read test output file: {e}")
        raise


def check_for_failures(test_output: str) -> bool:
    """
    Check if there are any test failures in the output.

    Args:
        test_output: The test output to check

    Returns:
        True if failures detected, False otherwise
    """
    failure_indicators = ["FAILED", "ERROR", "ERRORS", "error"]
    return any(indicator in test_output for indicator in failure_indicators)


def main():
    """Run the SRE error debugger agent."""
    logger.info("Starting SRE error debugging process...")

    # Validate required environment variables
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        logger.error("LLM_API_KEY environment variable is not set.")
        sys.exit(1)

    # Get configuration
    test_path = os.getenv("TEST_PATH", "tests/")
    test_output_file = os.getenv("TEST_OUTPUT_FILE")
    repo_name = get_repo_name()

    logger.info(f"Repository: {repo_name}")
    logger.info(f"Test path: {test_path}")

    try:
        # Get test output - either from file or by running tests
        if test_output_file:
            logger.info(f"Loading test output from {test_output_file}")
            test_output = load_test_output(test_output_file)
        else:
            logger.info("Running tests to capture failures...")
            test_output = run_tests_and_capture_output(test_path)

        # Check if there are any failures
        if not check_for_failures(test_output):
            logger.info("No test failures detected. Nothing to debug!")
            print("\n✅ No test failures found. All tests passing!")
            return

        logger.info("Test failures detected. Starting analysis...")

        # Truncate output if too long (keep last 5000 chars to show recent errors)
        if len(test_output) > 5000:
            logger.warning(
                f"Test output is large ({len(test_output)} chars), "
                "truncating to last 5000 chars"
            )
            test_output = "...\n[Output truncated]\n...\n" + test_output[-5000:]

        # Create the debugging prompt
        prompt = PROMPT.format(
            test_output=test_output,
            current_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            repo_name=repo_name,
            test_path=test_path,
        )

        # Configure LLM
        model = os.getenv("LLM_MODEL", "openhands/claude-sonnet-4-5-20250929")
        base_url = os.getenv("LLM_BASE_URL")

        llm_config = {
            "model": model,
            "api_key": api_key,
            "service_id": "sre_debugger",
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

        logger.info("Starting error analysis...")
        logger.info("Agent will examine test failures and investigate codebase")

        # Send the prompt and run the agent
        conversation.send_message(prompt)
        conversation.run()

        logger.info("Error analysis completed successfully")

        # Check if ERROR_ANALYSIS.md was created
        if os.path.exists("ERROR_ANALYSIS.md"):
            logger.info("ERROR_ANALYSIS.md file created")

            # Show summary
            with open("ERROR_ANALYSIS.md") as f:
                first_lines = "".join(f.readlines()[:20])
            print("\n" + "=" * 60)
            print("Error Analysis Report Generated!")
            print("=" * 60)
            print(first_lines)
            print("\n[... see ERROR_ANALYSIS.md for full report ...]")
        else:
            logger.warning("ERROR_ANALYSIS.md file was not created")

    except Exception as e:
        logger.error(f"Error debugging failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
