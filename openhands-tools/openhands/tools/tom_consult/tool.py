"""Tom consultation tool definition."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, override

from openhands.sdk.io import LocalFileStore
from openhands.sdk.tool import ToolDefinition
from openhands.tools.tom_consult.action import ConsultTomAction, SleeptimeComputeAction
from openhands.tools.tom_consult.executor import TomConsultExecutor
from openhands.tools.tom_consult.observation import (
    ConsultTomObservation,
    SleeptimeComputeObservation,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


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
