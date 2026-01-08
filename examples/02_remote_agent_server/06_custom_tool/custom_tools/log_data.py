"""Log Data Tool - Example custom tool for logging structured data to JSON.

This tool demonstrates how to create a custom tool that logs structured data
to a local JSON file during agent execution. The data can be retrieved and
verified after the agent completes.
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import Field

from openhands.sdk import (
    Action,
    ImageContent,
    Observation,
    TextContent,
    ToolDefinition,
)
from openhands.sdk.tool import ToolExecutor, register_tool


# --- Enums and Models ---


class LogLevel(str, Enum):
    """Log level for entries."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class LogDataAction(Action):
    """Action to log structured data to a JSON file."""

    message: str = Field(description="The log message")
    level: LogLevel = Field(
        default=LogLevel.INFO,
        description="Log level (debug, info, warning, error)",
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional structured data to include in the log entry",
    )


class LogDataObservation(Observation):
    """Observation returned after logging data."""

    success: bool = Field(description="Whether the data was successfully logged")
    log_file: str = Field(description="Path to the log file")
    entry_count: int = Field(description="Total number of entries in the log file")

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        """Convert observation to LLM content."""
        if self.success:
            return [
                TextContent(
                    text=(
                        f"✅ Data logged successfully to {self.log_file}\n"
                        f"Total entries: {self.entry_count}"
                    )
                )
            ]
        return [TextContent(text="❌ Failed to log data")]


# --- Executor ---

# Default log file path
DEFAULT_LOG_FILE = "/tmp/agent_data.json"


class LogDataExecutor(ToolExecutor[LogDataAction, LogDataObservation]):
    """Executor that logs structured data to a JSON file."""

    def __init__(self, log_file: str = DEFAULT_LOG_FILE):
        """Initialize the log data executor.

        Args:
            log_file: Path to the JSON log file
        """
        self.log_file = Path(log_file)

    def __call__(
        self,
        action: LogDataAction,
        conversation=None,  # noqa: ARG002
    ) -> LogDataObservation:
        """Execute the log data action.

        Args:
            action: The log data action
            conversation: Optional conversation context (not used)

        Returns:
            LogDataObservation with the result
        """
        # Load existing entries or start fresh
        entries: list[dict[str, Any]] = []
        if self.log_file.exists():
            try:
                with open(self.log_file) as f:
                    entries = json.load(f)
            except (json.JSONDecodeError, OSError):
                entries = []

        # Create new entry with timestamp
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": action.level.value,
            "message": action.message,
            "data": action.data,
        }
        entries.append(entry)

        # Write back to file
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_file, "w") as f:
            json.dump(entries, f, indent=2)

        return LogDataObservation(
            success=True,
            log_file=str(self.log_file),
            entry_count=len(entries),
        )


# --- Tool Definition ---

_LOG_DATA_DESCRIPTION = """Log structured data to a JSON file.

Use this tool to record information, findings, or events during your work.
Each log entry includes a timestamp and can contain arbitrary structured data.

Parameters:
* message: A descriptive message for the log entry
* level: Log level - one of 'debug', 'info', 'warning', 'error' (default: info)
* data: Optional dictionary of additional structured data to include

Example usage:
- Log a finding: message="Found potential issue", level="warning", data={"file": "app.py", "line": 42}
- Log progress: message="Completed analysis", level="info", data={"files_checked": 10}
"""  # noqa: E501


class LogDataTool(ToolDefinition[LogDataAction, LogDataObservation]):
    """Tool for logging structured data to a JSON file."""

    @classmethod
    def create(cls, conv_state, **params) -> Sequence[ToolDefinition]:  # noqa: ARG003
        """Create LogDataTool instance.

        Args:
            conv_state: Conversation state (not used in this example)
            **params: Additional parameters:
                - log_file: Path to the JSON log file (default: /tmp/agent_data.json)

        Returns:
            A sequence containing a single LogDataTool instance
        """
        log_file = params.get("log_file", DEFAULT_LOG_FILE)
        executor = LogDataExecutor(log_file=log_file)

        return [
            cls(
                description=_LOG_DATA_DESCRIPTION,
                action_type=LogDataAction,
                observation_type=LogDataObservation,
                executor=executor,
            )
        ]


# Auto-register the tool when this module is imported
# This is what enables dynamic tool registration in the remote agent server
register_tool("LogDataTool", LogDataTool)
