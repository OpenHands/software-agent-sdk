"""Test that an agent can wait for a background process to finish.

This integration test verifies that the agent can:
1. Start a background process with output redirection
2. Capture the PID and wait for completion using tail --pid
3. Check the exit status after the process completes
"""

import os

from openhands.sdk import get_logger
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult


INSTRUCTION = (
    "Run the script 'long_task.sh' in the background using 'nohup'. Redirect output "
    "to 'output.log'. Capture the PID using '$!' and then wait for it to finish using "
    "the shell 'wait' builtin. After it finishes, tell me the final result from the "
    "output file and whether the script succeeded or failed based on the exit code "
    "from 'wait'."
)

# A script that takes a few seconds to complete and writes output
SCRIPT_CONTENT = """#!/bin/bash
echo "Starting long task..."
sleep 1
echo "Step 1 complete"
sleep 1
echo "Step 2 complete"
sleep 1
echo "FINAL_RESULT: SUCCESS_12345"
exit 0
"""


logger = get_logger(__name__)


class WaitForBackgroundProcessTest(BaseIntegrationTest):
    """Test that an agent can wait for a background process to finish."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.script_path: str = os.path.join(self.workspace, "long_task.sh")
        self.output_path: str = os.path.join(self.workspace, "output.log")

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
        """Create the long-running script for the agent to execute."""
        with open(self.script_path, "w") as f:
            f.write(SCRIPT_CONTENT)
        os.chmod(self.script_path, 0o755)
        logger.info(f"Created long-running script at {self.script_path}")

    def verify_result(self) -> TestResult:
        """Verify that the agent waited for the process and got the result."""
        # Check if the script was executed (output file should exist)
        if not os.path.exists(self.output_path):
            # The agent might have used a different output file name
            # Check if any log file exists with the expected content
            for filename in os.listdir(self.workspace):
                filepath = os.path.join(self.workspace, filename)
                if os.path.isfile(filepath) and filename.endswith(".log"):
                    with open(filepath) as f:
                        content = f.read()
                    if "FINAL_RESULT: SUCCESS_12345" in content:
                        return TestResult(
                            success=True,
                            reason=(
                                "Agent successfully waited for background process "
                                "and captured the final result"
                            ),
                        )

            # Check the agent's conversation for evidence it got the result
            for event in self.collected_events:
                event_str = str(event)
                if "SUCCESS_12345" in event_str:
                    return TestResult(
                        success=True,
                        reason=(
                            "Agent successfully waited for background process "
                            "and reported the final result"
                        ),
                    )

            return TestResult(
                success=False,
                reason="No evidence that agent waited for and captured process output",
            )

        # Output file exists at expected path
        with open(self.output_path) as f:
            content = f.read()

        if "FINAL_RESULT: SUCCESS_12345" not in content:
            return TestResult(
                success=False,
                reason=f"Output file missing expected result. Content: {content}",
            )

        return TestResult(
            success=True,
            reason=(
                "Agent successfully waited for background process "
                "and captured the final result"
            ),
        )
