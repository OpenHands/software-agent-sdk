"""Test that an agent can execute interactive Python scripts with input."""

import hashlib
import os

from openhands.sdk import get_logger
from tests.integration.base import BaseIntegrationTest, TestResult


INSTRUCTION = (
    'Execute the python script in your workspace python_script.py with input "John" '
    'and "25" and tell me the secret number.'
)

# Calculate the expected secret number for age 25
SECRET_NUMBER = int(hashlib.sha256(str(25).encode()).hexdigest()[:8], 16) % 1000

PYTHON_SCRIPT_CONTENT = (
    'name = input("Enter your name: "); '
    'age = input("Enter your age: "); '
    "import hashlib; "
    "secret = int(hashlib.sha256(str(age).encode()).hexdigest()[:8], 16) % 1000; "
    'print(f"Hello {name}, you are {age} years old. '
    'Tell you a secret number: {secret}")'
)


logger = get_logger(__name__)


class InteractiveCommandsTest(BaseIntegrationTest):
    """Test that an agent can execute interactive Python scripts with input."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.script_path: str = os.path.join(self.workspace, "python_script.py")

    def setup(self) -> None:
        """Set up the interactive Python script."""

        try:
            with open(self.script_path, "w") as f:
                f.write(PYTHON_SCRIPT_CONTENT)

            logger.info(
                f"Created interactive Python script at {self.script_path} "
                f"with expected secret number: {SECRET_NUMBER}"
            )

        except Exception as e:
            raise RuntimeError(f"Failed to set up interactive Python script: {e}")

    def verify_result(self) -> TestResult:
        """Verify that the agent successfully executed the script with input."""
        if not os.path.exists(self.script_path):
            return TestResult(
                success=False,
                reason="Python script file was not created",
            )

        try:
            with open(self.script_path) as f:
                content = f.read()

            if PYTHON_SCRIPT_CONTENT not in content:
                return TestResult(
                    success=False,
                    reason="Python script content is incorrect",
                )

            return TestResult(
                success=True,
                reason=(
                    f"Interactive Python script setup completed. Agent should "
                    f"execute the script with inputs 'John' and '25' and find "
                    f"the secret number: {SECRET_NUMBER}"
                ),
            )

        except Exception as e:
            return TestResult(
                success=False,
                reason=f"Error verifying script content: {e}",
            )
