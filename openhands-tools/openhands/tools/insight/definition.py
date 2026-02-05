"""Insight tool definition.

This module provides the InsightTool for analyzing conversation sessions
and generating usage reports with optimization suggestions.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, override

from pydantic import Field

from openhands.sdk.io import LocalFileStore
from openhands.sdk.llm import ImageContent, TextContent
from openhands.sdk.tool import Action, Observation, ToolDefinition, register_tool


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


# ==================== Action Schema ====================


class InsightAction(Action):
    """Action to generate session insights and usage report."""

    generate_html: bool = Field(
        default=True,
        description="Whether to generate an HTML report dashboard",
    )
    suggest_skills: bool = Field(
        default=True,
        description="Whether to suggest new skills based on usage patterns",
    )
    max_sessions: int = Field(
        default=50,
        description="Maximum number of recent sessions to analyze",
    )


# ==================== Observation Schema ====================


class InsightObservation(Observation):
    """Observation from insight analysis."""

    summary: str = Field(
        default="", description="Summary of the session analysis"
    )
    sessions_analyzed: int = Field(
        default=0, description="Number of sessions analyzed"
    )
    common_patterns: list[str] = Field(
        default_factory=list, description="Common usage patterns identified"
    )
    bottlenecks: list[str] = Field(
        default_factory=list, description="Identified bottlenecks or issues"
    )
    suggestions: list[str] = Field(
        default_factory=list, description="Optimization suggestions"
    )
    report_path: str | None = Field(
        default=None, description="Path to generated HTML report"
    )

    @property
    @override
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        """Convert observation to LLM-readable content."""
        parts = []

        if self.summary:
            parts.append(f"## Session Analysis Summary\n{self.summary}")

        parts.append(f"\n**Sessions Analyzed:** {self.sessions_analyzed}")

        if self.common_patterns:
            parts.append("\n### Common Patterns")
            for pattern in self.common_patterns:
                parts.append(f"- {pattern}")

        if self.bottlenecks:
            parts.append("\n### Identified Bottlenecks")
            for bottleneck in self.bottlenecks:
                parts.append(f"- {bottleneck}")

        if self.suggestions:
            parts.append("\n### Optimization Suggestions")
            for i, suggestion in enumerate(self.suggestions, 1):
                parts.append(f"{i}. {suggestion}")

        if self.report_path:
            parts.append(f"\n**HTML Report:** {self.report_path}")

        return [TextContent(text="\n".join(parts))]


# ==================== Tool Description ====================

_INSIGHT_DESCRIPTION = """Analyze conversation history and generate usage insights.

This tool scans historical session data to identify:
- Common usage patterns and workflows
- Bottlenecks and recurring issues
- Opportunities for optimization

Use this tool when:
- User requests '/insight' or wants to analyze their usage
- You need to understand user patterns for personalization
- User wants suggestions for workflow improvements

The tool can generate an HTML dashboard report and suggest new skills
to automate repetitive tasks."""


# ==================== Tool Definition ====================


class InsightTool(ToolDefinition[InsightAction, InsightObservation]):
    """Tool for analyzing sessions and generating insights."""

    @classmethod
    @override
    def create(
        cls,
        conv_state: "ConversationState",
        llm_model: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> Sequence[ToolDefinition[Any, Any]]:
        """Initialize insight tool with executor parameters.

        Args:
            conv_state: Conversation state (required by registry)
            llm_model: LLM model to use for analysis
            api_key: API key for LLM
            api_base: Base URL for LLM

        Returns:
            Sequence containing InsightTool instance
        """
        # conv_state required by registry but not used - state passed at runtime
        _ = conv_state

        # Import here to avoid circular imports
        from openhands.tools.insight.executor import InsightExecutor

        file_store = LocalFileStore(root="~/.openhands")

        executor = InsightExecutor(
            file_store=file_store,
            llm_model=llm_model,
            api_key=api_key,
            api_base=api_base,
        )

        return [
            cls(
                description=_INSIGHT_DESCRIPTION,
                action_type=InsightAction,
                observation_type=InsightObservation,
                executor=executor,
            )
        ]


# Automatically register the tool when this module is imported
register_tool(InsightTool.name, InsightTool)
