"""Events related to security analyzer configuration."""

from typing import TYPE_CHECKING

from pydantic import Field
from rich.text import Text

from openhands.sdk.event.base import Event
from openhands.sdk.event.types import SourceType


if TYPE_CHECKING:
    from openhands.sdk.security.analyzer import SecurityAnalyzerBase


class SecurityAnalyzerConfigurationEvent(Event):
    """Event indicating the current SecurityAnalyzer configuration status.

    This event is emitted during agent initialization to track whether
    a SecurityAnalyzer has been configured and what type it is.
    """

    source: SourceType = "agent"
    analyzer_type: str | None = Field(
        default=None,
        description=(
            "The type of security analyzer configured, or None if not configured"
        ),
    )

    @classmethod
    def from_analyzer(
        cls, analyzer: "SecurityAnalyzerBase | None" = None
    ) -> "SecurityAnalyzerConfigurationEvent":
        """Create a SecurityAnalyzerConfigurationEvent from a SecurityAnalyzer instance.

        Args:
            analyzer: The SecurityAnalyzer instance, or None if not configured

        Returns:
            A SecurityAnalyzerConfigurationEvent with the appropriate analyzer_type
        """
        if analyzer is None:
            analyzer_type = None
        else:
            analyzer_type = analyzer.__class__.__name__

        return cls(analyzer_type=analyzer_type)

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this security analyzer configuration event."""  # type: ignore[misc]
        content = Text()
        content.append("Security Analyzer Configuration", style="bold cyan")
        if self.analyzer_type:
            content.append(f"\n  Type: {self.analyzer_type}", style="green")
        else:
            content.append("\n  Type: None (not configured)", style="yellow")
        return content

    def __str__(self) -> str:
        """Plain text string representation for SecurityAnalyzerConfigurationEvent."""
        if self.analyzer_type:
            return (
                f"{self.__class__.__name__} ({self.source}): "
                f"{self.analyzer_type} configured"
            )
        else:
            return (
                f"{self.__class__.__name__} ({self.source}): "
                f"No security analyzer configured"
            )
