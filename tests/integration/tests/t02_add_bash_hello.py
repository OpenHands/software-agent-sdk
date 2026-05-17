"""Test that an agent can write a shell script that prints 'hello'."""

import os

from openhands.sdk import get_logger
from tests.integration.base import BaseIntegrationTest, TestResult


INSTRUCTION = "Write a shell script 'shell/hello.sh' that prints 'hello'."


logger = get_logger(__name__)


class BashHelloTest(BaseIntegrationTest):
    """Test that an agent can write a shell script that prints 'hello'."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.script_path: str = os.path.join(self.workspace, "shell", "hello.sh")

    def setup(self) -> None:
        """Setup is not needed - agent will create directories as needed."""

    def verify_result(self) -> TestResult:
        """Verify that the agent successfully created the shell script."""
        if not os.path.exists(self.script_path):
            return TestResult(
                success=False, reason="Shell script 'shell/hello.sh' not found"
            )

        # Check if the script is executable
        if not os.access(self.script_path, os.X_OK):
            return TestResult(success=False, reason="Shell script is not executable")

        # Read the script content
        with open(self.script_path) as f:
            script_content = f.read()

        # Check if the script contains the expected output
        if "hello" not in script_content.lower():
            return TestResult(
                success=False,
                reason=f"Script does not contain 'hello': {script_content}",
            )

        # Try to execute the script and check output
        try:
            import subprocess

            result = subprocess.run(
                ["bash", self.script_path],
                capture_output=True,
                text=True,
                cwd=self.workspace,
            )
            if result.returncode != 0:
                return TestResult(
                    success=False,
                    reason=f"Script execution failed: {result.stderr}",
                )

            output = result.stdout.strip()
            if "hello" not in output.lower():
                return TestResult(
                    success=False,
                    reason=f"Script output does not contain 'hello': {output}",
                )

            return TestResult(
                success=True,
                reason=f"Successfully created and executed script: {output}",
            )

        except Exception as e:
            return TestResult(
                success=False, reason=f"Failed to execute script: {str(e)}"
            )
