"""
Base classes for agent-sdk integration tests.
"""

import os
import sys
from abc import ABC, abstractmethod
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from typing import Any

from pydantic import BaseModel, SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Message,
    TextContent,
)
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.visualizer import DefaultConversationVisualizer
from openhands.sdk.event.base import Event
from openhands.sdk.event.llm_convertible import (
    MessageEvent,
)
from openhands.sdk.tool import Tool


class SkipTest(Exception):
    """
    Exception raised to indicate that a test should be skipped.

    This is useful for tests that require specific capabilities (e.g., vision)
    that may not be available in all LLMs.
    """

    pass


class TestResult(BaseModel):
    """Result of an integration test."""

    success: bool
    reason: str | None = None
    skipped: bool = False


class BaseIntegrationTest(ABC):
    """
    Base class for agent-sdk integration tests.

    This class provides a structured approach to writing integration tests
    that use real LLM calls. It handles common setup like LLM configuration,
    temporary directory management, and agent creation.

    Unlike the OpenHands approach which uses a Runtime, this uses tools
    directly with temporary directories for isolation.
    """

    INSTRUCTION: str
    CRITICALITY: str = (
        "critical"  # Default to critical; can be overridden in subclasses
    )

    def __init__(
        self,
        instruction: str,
        llm_config: dict[str, Any],
        instance_id: str,
        workspace: str,
    ):
        self.instruction: str = instruction
        self.llm_config: dict[str, Any] = llm_config
        self.workspace: str = workspace
        self.instance_id: str = instance_id
        api_key = os.getenv("LLM_API_KEY")
        if not api_key:
            raise ValueError(
                "LLM_API_KEY environment variable not set. Skipping real LLM test."
            )
        base_url = os.getenv("LLM_BASE_URL")
        if not base_url:
            raise ValueError(
                "LLM_BASE_URL environment variable not set. Skipping real LLM test."
            )

        # Create LLM with all config parameters
        llm_kwargs = {
            **self.llm_config,  # Pass through all config parameters
            "base_url": base_url,
            "api_key": SecretStr(api_key),
        }

        self.llm: LLM = LLM(**llm_kwargs, usage_id="test-llm")
        self.agent: Agent = Agent(llm=self.llm, tools=self.tools)
        self.collected_events: list[Event] = []
        self.llm_messages: list[dict[str, Any]] = []

        # Create log file path for this test instance
        self.log_file_path: str = os.path.join(
            self.workspace, f"{self.instance_id}_agent_logs.txt"
        )

        self.conversation: LocalConversation = LocalConversation(
            agent=self.agent,
            workspace=self.workspace,
            callbacks=[self.conversation_callback],
            visualizer=DefaultConversationVisualizer(),  # Use default visualizer
        )

    def conversation_callback(self, event: Event):
        """Callback to collect conversation events."""
        self.collected_events.append(event)
        if isinstance(event, MessageEvent):
            self.llm_messages.append(event.llm_message.model_dump())

    def run_instruction(self) -> TestResult:
        """
        Run user instruction through the agent and verify results.

        Returns:
            TestResult: The result of the test
        """
        try:
            # Setup
            self.setup()

            # Initialize log file with header
            with open(self.log_file_path, "w") as f:
                f.write(f"Agent Logs for Test: {self.instance_id}\n")
                f.write("=" * 50 + "\n\n")

            # Capture stdout and stderr during conversation
            stdout_buffer = StringIO()
            stderr_buffer = StringIO()

            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                self.conversation.send_message(
                    message=Message(
                        role="user", content=[TextContent(text=self.instruction)]
                    )
                )
                self.conversation.run()

            # Save captured output to log file
            captured_output = stdout_buffer.getvalue()
            captured_errors = stderr_buffer.getvalue()

            with open(self.log_file_path, "a") as f:
                if captured_output:
                    f.write("STDOUT:\n")
                    f.write(captured_output)
                    f.write("\n")
                if captured_errors:
                    f.write("STDERR:\n")
                    f.write(captured_errors)
                    f.write("\n")

            # Also print to console for debugging
            if captured_output:
                print(captured_output, end="")
            if captured_errors:
                print(captured_errors, file=sys.stderr, end="")

            # Verify results
            result = self.verify_result()

            return result

        except Exception as e:
            return TestResult(success=False, reason=f"Test execution failed: {str(e)}")

        finally:
            self.teardown()

    @property
    @abstractmethod
    def tools(self) -> list[Tool]:
        """List of tools available to the agent."""
        pass

    @abstractmethod
    def setup(self) -> None:
        """
        Initialize test-specific setup.

        This method should create any files, directories, or other
        resources needed for the test.
        """
        pass

    @abstractmethod
    def verify_result(self) -> TestResult:
        """
        Verify the result of the test.

        This method should check if the agent successfully completed
        the task by examining files in self.temp_dir, checking the
        events in self.events, or other verification methods.

        Returns:
            TestResult: The result of the verification
        """
        pass

    def teardown(self):
        """
        Clean up test resources.
        The workspace directory is torn down externally.
        Add any additional cleanup (git, server, ...) here if needed.
        """

    # ===== Behavior Check Helper Methods =====
    # These methods help verify agent behavior patterns and adherence to system messages

    def find_tool_calls(self, tool_name: str) -> list[Event]:
        """
        Find all ActionEvents where a specific tool was called.

        Args:
            tool_name: Name of the tool to search for
                (e.g., "FileEditorTool", "TerminalTool")

        Returns:
            List of ActionEvents matching the tool name
        """
        from openhands.sdk.event import ActionEvent

        return [
            event
            for event in self.collected_events
            if isinstance(event, ActionEvent) and event.tool_name == tool_name
        ]

    def find_file_editing_operations(self) -> list[Event]:
        """
        Find all file editing operations (create, str_replace, insert, undo_edit).

        Excludes read-only operations like 'view'.

        Returns:
            List of ActionEvents that performed file editing
        """
        from openhands.sdk.event import ActionEvent

        editing_operations = []
        for event in self.collected_events:
            if isinstance(event, ActionEvent) and event.tool_name == "FileEditorTool":
                if event.action is not None:
                    # Check if the command is an editing operation
                    command = getattr(event.action, "command", None)
                    if command in ["create", "str_replace", "insert", "undo_edit"]:
                        editing_operations.append(event)
        return editing_operations

    def find_file_operations(self, file_pattern: str | None = None) -> list[Event]:
        """
        Find all file operations (both read and write).

        Args:
            file_pattern: Optional pattern to match against file paths
                (e.g., "*.md", "README")

        Returns:
            List of ActionEvents that performed file operations
        """
        from openhands.sdk.event import ActionEvent

        file_operations = []
        for event in self.collected_events:
            if isinstance(event, ActionEvent) and event.tool_name == "FileEditorTool":
                if event.action is not None:
                    path = getattr(event.action, "path", None)
                    if file_pattern is None or (
                        path and self._matches_pattern(path, file_pattern)
                    ):
                        file_operations.append(event)
        return file_operations

    def check_bash_command_used(self, command_pattern: str) -> list[Event]:
        """
        Check if agent used bash commands instead of specialized tools.

        Args:
            command_pattern: Pattern to search for in bash commands (e.g., "cat", "sed")

        Returns:
            List of ActionEvents where bash was used with the pattern
        """
        from openhands.sdk.event import ActionEvent

        bash_commands = []
        for event in self.collected_events:
            if isinstance(event, ActionEvent) and event.tool_name == "TerminalTool":
                if event.action is not None:
                    command = getattr(event.action, "command", "")
                    if command_pattern in command:
                        bash_commands.append(event)
        return bash_commands

    def get_conversation_summary(self, max_length: int = 5000) -> str:
        """
        Get a summary of the conversation including agent thoughts and actions.

        Args:
            max_length: Maximum length of the summary

        Returns:
            String summary of the conversation
        """
        from openhands.sdk.event import ActionEvent, MessageEvent

        summary_parts = []
        for event in self.collected_events:
            if isinstance(event, MessageEvent):
                role = event.llm_message.role
                from openhands.sdk.llm import TextContent as TextContentType

                content = ""
                if event.llm_message.content:
                    content = " ".join(
                        [
                            c.text
                            for c in event.llm_message.content
                            if isinstance(c, TextContentType)
                        ]
                    )
                if content:
                    summary_parts.append(f"[{role.upper()}] {content[:500]}")
            elif isinstance(event, ActionEvent):
                thought = " ".join([t.text for t in event.thought])
                tool = event.tool_name
                summary_parts.append(f"[ACTION] Tool: {tool}, Thought: {thought[:300]}")

        summary = "\n".join(summary_parts)
        if len(summary) > max_length:
            summary = summary[:max_length] + "..."
        return summary

    def _matches_pattern(self, path: str, pattern: str) -> bool:
        """Helper to match file paths against patterns."""
        import fnmatch

        return fnmatch.fnmatch(path, pattern) or pattern in path
