"""Test that an agent can fix typos in a text file using BaseIntegrationTest."""

import os

from openhands.sdk import get_logger
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult


INSTRUCTION = (
    "Please fix all the typos in the file 'document.txt' that is in "
    "the current directory. "
    "Read the file first, identify the typos, and correct them. "
)

TYPO_CONTENT = """
This is a sample documnet with three typos that need to be fixed.
The purpse of this document is to test the agent's ability to correct spelling mistakes.
Please fix all the mispelled words in this document.
"""


logger = get_logger(__name__)


class TypoFixTest(BaseIntegrationTest):
    """Test that an agent can fix typos in a text file."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.document_path: str = os.path.join(self.workspace, "document.txt")

    @property
    def tools(self) -> list[Tool]:
        """List of tools available to the agent."""
        register_tool("TerminalTool", TerminalTool)
        register_tool("FileEditorTool", FileEditorTool)
        return [
            Tool(name="TerminalTool"),
            Tool(name="FileEditorTool"),
        ]

    def setup(self) -> None:
        """Create a text file with typos for the agent to fix."""
        # Create the test file with typos
        typo_content = TYPO_CONTENT
        with open(self.document_path, "w") as f:
            f.write(typo_content)

        logger.info(f"Created test document with typos at: {self.document_path}")

    def verify_result(self) -> TestResult:
        """Verify that the agent successfully fixed the typos."""
        if not os.path.exists(self.document_path):
            return TestResult(
                success=False, reason="Document file not found after agent execution"
            )
        with open(self.document_path) as f:
            corrected_content = f.read()

        are_typos_fixed: bool = (
            "document" in corrected_content
            and "purpose" in corrected_content
            and "misspelled" in corrected_content
        )
        if are_typos_fixed:
            return TestResult(success=True, reason="Successfully fixed all typos")
        else:
            return TestResult(
                success=False,
                reason=f"Typos were not fully corrected:\n{corrected_content}",
            )
