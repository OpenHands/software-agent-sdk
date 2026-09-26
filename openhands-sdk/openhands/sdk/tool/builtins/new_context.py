"""Request a context reset through the agent's normal event lifecycle."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Self

from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
    from openhands.sdk.conversation.state import ConversationState


class NewContextAction(Action):
    """Request condensation after saving progress in context_notes."""


class NewContextObservation(Observation):
    """Successful requests are turned into reset events by the agent."""


class NewContextExecutor(ToolExecutor[NewContextAction, NewContextObservation]):
    def __call__(
        self,
        action: NewContextAction,  # noqa: ARG002
        conversation: "LocalConversation | None" = None,
    ) -> NewContextObservation:
        if conversation is None:
            return NewContextObservation.from_text(
                "A context reset requires a conversation.", is_error=True
            )
        condenser = conversation.agent.condenser
        if condenser is None or not condenser.handles_condensation_requests():
            return NewContextObservation.from_text(
                "The configured condenser does not support context reset requests.",
                is_error=True,
            )
        return NewContextObservation.from_text("Context reset requested.")


class NewContextTool(ToolDefinition[NewContextAction, NewContextObservation]):
    """Optional tool for agent-requested context condensation."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        **params,
    ) -> Sequence[Self]:
        if params:
            raise ValueError("NewContextTool does not accept parameters")
        return [
            cls(
                description=(
                    "Start a new context window using the configured condenser. "
                    "First save progress, decisions, next steps and useful event IDs "
                    "with context_notes. The reset takes effect after this tool "
                    "batch finishes. "
                    "Earlier history and notes remain retrievable after the reset."
                ),
                action_type=NewContextAction,
                observation_type=NewContextObservation,
                executor=NewContextExecutor(),
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]
