"""Tom consultation tool definition.

This module provides tools for consulting Tom agent for personalized guidance
based on user modeling, and for indexing conversations for user modeling.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, override

from pydantic import Field

from openhands.sdk.io import LocalFileStore
from openhands.sdk.llm import ImageContent, TextContent
from openhands.sdk.tool import Action, Observation, ToolDefinition, register_tool


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


# ==================== Action Schemas ====================


class ConsultTomAction(Action):
    """Action to consult Tom agent for guidance."""

    reason: str = Field(
        description="Brief explanation of why you need Tom agent consultation"
    )
    use_user_message: bool = Field(
        default=True,
        description=(
            "Whether to consult about the user message (True) "
            "or provide custom query (False)"
        ),
    )
    custom_query: str | None = Field(
        default=None,
        description=(
            "Custom query to ask Tom agent (only used when use_user_message is False)"
        ),
    )


class SleeptimeComputeAction(Action):
    """Action to index existing conversations for Tom's user modeling.

    This triggers Tom agent's sleeptime_compute function which processes
    conversation history to build and update the user model.
    """

    pass


# ==================== Observation Schemas ====================


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
            text = (
                f"Successfully indexed {self.sessions_processed} "
                f"conversation(s) for user modeling.\n{self.message}"
            )
        else:
            text = f"Sleeptime compute completed.\n{self.message}"

        return [TextContent(text=text)]


# ==================== Tool Descriptions ====================

_CONSULT_DESCRIPTION = """Consult Tom agent for guidance when you need help \
understanding user intent or task requirements.

This tool allows you to consult Tom agent for personalized guidance \
based on user modeling. Use this when:
- User instructions are vague or unclear
- You need help understanding what the user actually wants
- You want guidance on the best approach for the current task
- You have your own question for Tom agent about the task or user's needs

By default, Tom agent will analyze the user's message. \
Optionally, you can ask a custom question."""

_SLEEPTIME_DESCRIPTION = """Index the current conversation for Tom's user modeling.

This tool processes conversation history to build and update the user model. \
Use this to:
- Index conversations for future personalization
- Build user preferences and patterns from conversation history
- Update Tom's understanding of the user

This is typically used at the end of a conversation or when explicitly requested."""


# ==================== Tool Definitions ====================


class TomConsultTool(ToolDefinition[ConsultTomAction, ConsultTomObservation]):
    """Tool for consulting Tom agent."""

    @classmethod
    @override
    def create(
        cls,
        conv_state: "ConversationState",
        enable_rag: bool = True,
        llm_model: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> Sequence[ToolDefinition[Any, Any]]:
        """Initialize Tom consult tool with executor parameters.

        Args:
            conv_state: Conversation state (required by
            registry, state passed at runtime)
            enable_rag: Whether to enable RAG in Tom agent
            llm_model: LLM model to use for Tom agent
            api_key: API key for Tom agent's LLM
            api_base: Base URL for Tom agent's LLM

        Returns:
            Sequence containing TomConsultTool instance
        """
        # conv_state required by registry but not used - state passed at execution time
        _ = conv_state

        # Import here to avoid circular imports and make tom-swe optional
        from openhands.tools.tom_consult.executor import TomConsultExecutor

        file_store = LocalFileStore(root="~/.openhands")

        # Initialize the executor
        executor = TomConsultExecutor(
            file_store=file_store,
            enable_rag=enable_rag,
            llm_model=llm_model,
            api_key=api_key,
            api_base=api_base,
        )

        return [
            cls(
                description=_CONSULT_DESCRIPTION,
                action_type=ConsultTomAction,
                observation_type=ConsultTomObservation,
                executor=executor,
            )
        ]


class SleeptimeComputeTool(
    ToolDefinition[SleeptimeComputeAction, SleeptimeComputeObservation]
):
    """Tool for indexing conversations for Tom's user modeling."""

    @classmethod
    @override
    def create(
        cls,
        conv_state: "ConversationState",
        enable_rag: bool = True,
        llm_model: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> Sequence[ToolDefinition[Any, Any]]:
        """Initialize sleeptime compute tool with executor parameters.

        Args:
            conv_state: Conversation state (required by
            registry, state passed at runtime)
            enable_rag: Whether to enable RAG in Tom agent
            llm_model: LLM model to use for Tom agent
            api_key: API key for Tom agent's LLM
            api_base: Base URL for Tom agent's LLM

        Returns:
            Sequence containing SleeptimeComputeTool instance
        """
        # conv_state required by registry but not used - state passed at execution time
        _ = conv_state

        # Import here to avoid circular imports and make tom-swe optional
        from openhands.tools.tom_consult.executor import TomConsultExecutor

        file_store = LocalFileStore(root="~/.openhands")

        # Initialize the executor
        executor = TomConsultExecutor(
            file_store=file_store,
            enable_rag=enable_rag,
            llm_model=llm_model,
            api_key=api_key,
            api_base=api_base,
        )

        return [
            cls(
                description=_SLEEPTIME_DESCRIPTION,
                action_type=SleeptimeComputeAction,
                observation_type=SleeptimeComputeObservation,
                executor=executor,
            )
        ]


# Automatically register the tools when this module is imported
register_tool(TomConsultTool.name, TomConsultTool)
register_tool(SleeptimeComputeTool.name, SleeptimeComputeTool)
