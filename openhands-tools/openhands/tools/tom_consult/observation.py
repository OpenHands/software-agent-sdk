"""Observation schema for Tom consultation."""

from collections.abc import Sequence
from typing import override

from pydantic import Field

from openhands.sdk.llm import ImageContent, TextContent
from openhands.sdk.tool import Observation


class ConsultTomObservation(Observation):
    """Observation from Tom agent consultation."""

    suggestions: str = Field(
        default="", description="Tom agent's suggestions or guidance"
    )
    confidence: float | None = Field(
        default=None, description="Confidence score from Tom agent (0-1)"
    )
    reasoning: str | None = Field(
        default=None, description="Tom agent's reasoning for the suggestions"
    )

    @property
    @override
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        """Convert observation to LLM-readable content."""
        if not self.suggestions:
            return [TextContent(text="Tom agent did not provide suggestions.")]

        content_parts = [f"Tom agent's guidance:\n{self.suggestions}"]

        if self.reasoning:
            content_parts.append(f"\nReasoning: {self.reasoning}")

        if self.confidence is not None:
            content_parts.append(f"\nConfidence: {self.confidence:.0%}")

        return [TextContent(text="\n".join(content_parts))]


class SleeptimeComputeObservation(Observation):
    """Observation from sleeptime compute operation."""

    message: str = Field(
        default="", description="Result message from sleeptime compute"
    )
    sessions_processed: int = Field(
        default=0, description="Number of conversation sessions indexed"
    )

    @property
    @override
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        """Convert observation to LLM-readable content."""
        if self.sessions_processed > 0:
            text = f"Successfully indexed {self.sessions_processed} conversation(s) for user modeling.\n{self.message}"  # noqa: E501
        else:
            text = f"Sleeptime compute completed.\n{self.message}"

        return [TextContent(text=text)]
