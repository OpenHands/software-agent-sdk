"""Report Bug Tool - Example custom tool for structured data collection.

This tool demonstrates how to create a custom tool that collects structured data
during agent execution, which can be used for downstream processing like creating
Jira tickets or compiling bug reports.
"""

from collections.abc import Sequence
from enum import Enum

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


class BugSeverity(str, Enum):
    """Bug severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class BugAction(Action):
    """Action to report a bug with structured data."""

    title: str = Field(description="Short title describing the bug")
    description: str = Field(description="Detailed description of the bug")
    severity: BugSeverity = Field(
        description="Severity level of the bug (low, medium, high, critical)"
    )
    steps_to_reproduce: list[str] = Field(
        default_factory=list,
        description="List of steps to reproduce the bug",
    )
    expected_behavior: str | None = Field(
        default=None,
        description="What should happen instead",
    )
    actual_behavior: str | None = Field(
        default=None,
        description="What actually happens",
    )
    affected_files: list[str] = Field(
        default_factory=list,
        description="List of files affected by this bug",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Optional tags for categorizing the bug",
    )


class BugObservation(Observation):
    """Observation returned after reporting a bug."""

    bug_id: str = Field(description="Unique ID assigned to this bug report")
    success: bool = Field(description="Whether the bug was successfully reported")
    message: str = Field(description="Confirmation message")

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        """Convert observation to LLM content."""
        if self.success:
            return [
                TextContent(
                    text=(
                        f"✅ Bug reported successfully!\n"
                        f"Bug ID: {self.bug_id}\n"
                        f"Message: {self.message}"
                    )
                )
            ]
        return [TextContent(text=(f"❌ Failed to report bug\nMessage: {self.message}"))]


# --- Executor ---


class ReportBugExecutor(ToolExecutor[BugAction, BugObservation]):
    """Executor that collects bug reports."""

    def __init__(self):
        """Initialize the bug report collector."""
        self.bugs: list[dict] = []

    def __call__(self, action: BugAction, conversation=None) -> BugObservation:  # noqa: ARG002
        """Execute the bug report action.

        Args:
            action: The bug report action
            conversation: Optional conversation context (not used in this example)

        Returns:
            BugObservation with the result
        """
        # Generate a simple bug ID
        bug_id = f"BUG-{len(self.bugs) + 1:04d}"

        # Store the bug report
        bug_data = {
            "id": bug_id,
            "title": action.title,
            "description": action.description,
            "severity": action.severity.value,
            "steps_to_reproduce": action.steps_to_reproduce,
            "expected_behavior": action.expected_behavior,
            "actual_behavior": action.actual_behavior,
            "affected_files": action.affected_files,
            "tags": action.tags,
        }
        self.bugs.append(bug_data)

        return BugObservation(
            bug_id=bug_id,
            success=True,
            message=f"Bug report {bug_id} has been recorded",
        )

    def get_all_bugs(self) -> list[dict]:
        """Get all collected bug reports.

        Returns:
            List of bug report dictionaries
        """
        return self.bugs.copy()


# --- Tool Definition ---

_REPORT_BUG_DESCRIPTION = """Report a bug with structured data.

Use this tool to report bugs you discover during testing or analysis.
The bug report will be collected with structured data that can be used
to create tickets in issue tracking systems or compile into reports.

Required fields:
* title: Short, descriptive title of the bug
* description: Detailed description of what's wrong
* severity: One of 'low', 'medium', 'high', or 'critical'

Optional fields:
* steps_to_reproduce: List of steps to reproduce the issue
* expected_behavior: What should happen
* actual_behavior: What actually happens
* affected_files: Files related to this bug
* tags: Tags for categorization (e.g., 'ui', 'backend', 'performance')
"""


class ReportBugTool(ToolDefinition[BugAction, BugObservation]):
    """Tool for reporting bugs with structured data."""

    @classmethod
    def create(cls, conv_state, **params) -> Sequence[ToolDefinition]:  # noqa: ARG003
        """Create ReportBugTool instance.

        Args:
            conv_state: Conversation state (not used in this example)
            **params: Additional parameters (not used in this example)

        Returns:
            A sequence containing a single ReportBugTool instance
        """
        executor = ReportBugExecutor()

        return [
            cls(
                description=_REPORT_BUG_DESCRIPTION,
                action_type=BugAction,
                observation_type=BugObservation,
                executor=executor,
            )
        ]


# Auto-register the tool when this module is imported
# This is what enables dynamic tool registration in the remote agent server
register_tool("ReportBugTool", ReportBugTool)
