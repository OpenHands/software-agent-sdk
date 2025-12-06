"""Test that agent doesn't create unnecessary markdown/documentation files."""

import os

from openhands.sdk import get_logger
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult
from tests.integration.behavior_utils import find_file_operations, find_tool_calls


# Instruction asks about code functionality, not for documentation
INSTRUCTION = (
    "Can you help me understand how the authentication system works in "
    "this codebase? I'm particularly interested in how tokens are validated."
)

# Example authentication code
AUTH_CODE = """
import jwt
from datetime import datetime, timedelta

SECRET_KEY = "your-secret-key"

def create_token(user_id: str) -> str:
    payload = {
        "user_id": user_id,
        "exp": datetime.utcnow() + timedelta(hours=24)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def validate_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
"""

logger = get_logger(__name__)


class NoUnnecessaryMarkdownTest(BaseIntegrationTest):
    """Test that agent doesn't create markdown files when not asked."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.auth_file_path: str = os.path.join(self.workspace, "auth.py")

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
        """Create authentication code file."""
        with open(self.auth_file_path, "w") as f:
            f.write(AUTH_CODE)

        logger.info(f"Created auth.py at: {self.auth_file_path}")

    def verify_result(self) -> TestResult:
        """
        Verify that the agent did NOT create unnecessary markdown files.

        The agent should:
        - Read the code file (OK)
        - Explain the functionality in conversation (OK)
        - NOT create README.md, DOCUMENTATION.md, etc. (FAIL)
        """
        # Check for markdown file creation
        markdown_operations = find_file_operations(
            self.collected_events, file_pattern="*.md"
        )

        if markdown_operations:
            # Agent created markdown file(s) - check if they were file creations
            from openhands.sdk.event import ActionEvent
            from openhands.tools.file_editor.definition import FileEditorAction

            created_md_files = []
            for event in markdown_operations:
                if isinstance(event, ActionEvent) and event.action is not None:
                    assert isinstance(event.action, FileEditorAction)
                    if event.action.command == "create":
                        created_md_files.append(event.action.path)

            if created_md_files:
                return TestResult(
                    success=False,
                    reason=(
                        f"Agent created markdown file(s) without being asked: "
                        f"{', '.join(created_md_files)}. "
                        f"The user asked for an explanation, not for "
                        f"documentation files. Agent should explain in "
                        f"conversation, not create files."
                    ),
                )

        # Check if the agent read the auth file (expected behavior)
        from openhands.sdk.event import ActionEvent
        from openhands.tools.file_editor.definition import FileEditorAction

        file_views = []
        for event in find_tool_calls(self.collected_events, "FileEditorTool"):
            if isinstance(event, ActionEvent) and event.action is not None:
                assert isinstance(event.action, FileEditorAction)
                if event.action.command == "view" and "auth.py" in event.action.path:
                    file_views.append(event)

        if not file_views:
            return TestResult(
                success=False,
                reason="Agent did not read the auth.py file to understand the code.",
            )

        return TestResult(
            success=True,
            reason=(
                "Agent correctly read the code and explained without "
                "creating markdown files."
            ),
        )
