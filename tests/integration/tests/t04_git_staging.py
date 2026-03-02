"""Test that an agent can write a git commit message and commit changes."""

import os
import subprocess

from openhands.sdk import get_logger
from openhands.sdk.tool import Tool
from tests.integration.base import BaseIntegrationTest, TestResult, get_tools_for_preset


INSTRUCTION = (
    "Write a git commit message for the current staging area and commit the changes."
)


logger = get_logger(__name__)


class GitStagingTest(BaseIntegrationTest):
    """Test that an agent can write a git commit message and commit changes."""

    INSTRUCTION: str = INSTRUCTION

    @property
    def tools(self) -> list[Tool]:
        """List of tools available to the agent based on configured tool preset."""
        return get_tools_for_preset(self.tool_preset, enable_browser=False)

    def setup(self) -> None:
        """Set up git repository with staged changes."""
        # Initialize git repository
        subprocess.run(
            ["git", "init"], cwd=self.workspace, check=True, capture_output=True
        )

        # Configure git user (required for commits)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=self.workspace,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.workspace,
            check=True,
            capture_output=True,
        )

        # Create a Python file
        hello_py_path = os.path.join(self.workspace, "hello.py")
        with open(hello_py_path, "w") as f:
            f.write('print("hello world")\n')

        # Stage the file
        subprocess.run(
            ["git", "add", "hello.py"],
            cwd=self.workspace,
            check=True,
            capture_output=True,
        )

        logger.info("Set up git repository with staged hello.py file")

    def verify_result(self) -> TestResult:
        """Verify that the agent successfully committed the staged changes."""

        try:
            # Check git status to see if there are any staged changes left
            status_result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                check=True,
            )

            # If there are still staged changes, the commit didn't happen
            if "hello.py" in status_result.stdout.strip():
                return TestResult(
                    success=False,
                    reason=f"File to commit still staged: {status_result.stdout}",
                )

            # Check if there are any commits
            log_result = subprocess.run(
                ["git", "log", "--oneline"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                check=True,
            )

            if not log_result.stdout.strip():
                return TestResult(
                    success=False,
                    reason=f"No commits found in repository: {log_result.stdout}",
                )

            # Get the latest commit message
            commit_msg_result = subprocess.run(
                ["git", "log", "-1", "--pretty=format:%s"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                check=True,
            )

            commit_message = commit_msg_result.stdout.strip()

            # Verify the commit contains the hello.py file
            show_result = subprocess.run(
                ["git", "show", "--name-only", "--pretty=format:"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                check=True,
            )

            if "hello.py" not in show_result.stdout:
                return TestResult(
                    success=False,
                    reason="hello.py not found in the committed changes",
                )

            return TestResult(
                success=True,
                reason=(
                    f"Successfully committed changes with message: '{commit_message}'"
                ),
            )

        except subprocess.CalledProcessError as e:
            return TestResult(success=False, reason=f"Git command failed: {e}")
