"""Test that an agent can use Jupyter IPython to write a text file."""

import os

from openhands.sdk import get_logger
from openhands.sdk.tool import Tool
from tests.integration.base import BaseIntegrationTest, TestResult, get_tools_for_preset


INSTRUCTION = (
    "Use Jupyter IPython to write a text file in your workspace 'test.txt'"
    " containing 'hello world'."
)


logger = get_logger(__name__)


class JupyterWriteFileTest(BaseIntegrationTest):
    """Test that an agent can use Jupyter IPython to write a text file."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.file_path: str = os.path.join(self.workspace, "test.txt")

    @property
    def tools(self) -> list[Tool]:
        """List of tools available to the agent based on configured tool preset."""
        return get_tools_for_preset(self.tool_preset, enable_browser=False)

    def setup(self) -> None:
        """Setup is not needed - agent will create directories as needed."""

    def verify_result(self) -> TestResult:
        """Verify that the agent successfully created the text file using IPython."""
        if not os.path.exists(self.file_path):
            return TestResult(
                success=False, reason=f"Text file '{self.file_path}' not found"
            )

        # Read the file content
        with open(self.file_path) as f:
            file_content = f.read().strip()

        # Check if the file contains the expected content
        if "hello world" not in file_content.lower():
            return TestResult(
                success=False,
                reason=f"File does not contain 'hello world': {file_content}",
            )

        return TestResult(
            success=True,
            reason=f"Successfully created file with content: {file_content}",
        )
