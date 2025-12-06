"""Test that agent uses specialized tools instead of bash commands."""

import os

from openhands.sdk import get_logger
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult
from tests.integration.behavior_utils import (
    check_bash_command_used,
    find_tool_calls,
    get_conversation_summary,
)


INSTRUCTION = (
    "Please read the config.json file and tell me what the database host is set to."
)

CONFIG_CONTENT = """{
    "database": {
        "host": "localhost",
        "port": 5432,
        "name": "myapp_db"
    },
    "cache": {
        "enabled": true,
        "ttl": 3600
    }
}"""

logger = get_logger(__name__)


class UseSpecializedToolsTest(BaseIntegrationTest):
    """Test that agent uses FileEditorTool view instead of 'cat' command."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config_file_path: str = os.path.join(self.workspace, "config.json")

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
        """Create config file."""
        with open(self.config_file_path, "w") as f:
            f.write(CONFIG_CONTENT)

        logger.info(f"Created config.json at: {self.config_file_path}")

    def verify_result(self) -> TestResult:
        """
        Verify that the agent used FileEditorTool instead of bash 'cat'.

        The agent should:
        - Use FileEditorTool view command to read file (GOOD)
        - NOT use 'cat', 'head', 'tail', etc. via bash (BAD)
        """
        # Check if agent used bash commands to read files
        problematic_commands = ["cat", "head", "tail", "less", "more"]
        bash_file_reads = []

        for cmd in problematic_commands:
            events = check_bash_command_used(self.collected_events, cmd)
            if events:
                from openhands.sdk.event import ActionEvent
                from openhands.tools.terminal.definition import TerminalAction

                for event in events:
                    if isinstance(event, ActionEvent) and event.action is not None:
                        assert isinstance(event.action, TerminalAction)
                        # Check if config.json is in the command
                        if "config.json" in event.action.command:
                            bash_file_reads.append(event.action.command)

        if bash_file_reads:
            return TestResult(
                success=False,
                reason=(
                    f"Agent used bash commands to read files instead of "
                    f"FileEditorTool: {', '.join(bash_file_reads)}. "
                    f"Agent should use specialized tools like FileEditorTool "
                    f"view command instead of bash cat/head/tail for better "
                    f"user experience."
                ),
            )

        # Check if agent used FileEditorTool to read the file (expected behavior)
        from openhands.sdk.event import ActionEvent
        from openhands.tools.file_editor.definition import FileEditorAction

        file_reads = []
        for event in find_tool_calls(self.collected_events, "FileEditorTool"):
            if isinstance(event, ActionEvent) and event.action is not None:
                assert isinstance(event.action, FileEditorAction)
                if (
                    event.action.command == "view"
                    and "config.json" in event.action.path
                ):
                    file_reads.append(event)

        if not file_reads:
            # Agent didn't read the file at all - or used a different method
            # Check if they at least answered the question correctly
            conversation = get_conversation_summary(self.collected_events)
            if "localhost" in conversation:
                # They somehow got the answer, maybe through bash
                if bash_file_reads:
                    # Already handled above
                    pass
                return TestResult(
                    success=False,
                    reason=(
                        "Agent got the answer but did not use FileEditorTool "
                        "view command. This suggests they may have used an "
                        "alternative method."
                    ),
                )
            return TestResult(
                success=False,
                reason="Agent did not read the config.json file using FileEditorTool.",
            )

        return TestResult(
            success=True,
            reason=(
                "Agent correctly used FileEditorTool view command instead of bash cat."
            ),
        )
