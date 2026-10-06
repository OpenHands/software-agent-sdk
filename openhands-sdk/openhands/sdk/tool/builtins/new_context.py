"""Request a new model context after the current tool batch is committed."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Self

from pydantic import Field

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
    handoff: str = Field(
        default="",
        description=(
            "Your handoff to the next context: progress, remaining work, constraints, "
            "and useful file or history references. Kept without another LLM summary."
        ),
    )


class NewContextObservation(Observation):
    input_event_id: str | None = Field(
        default=None,
        description="History boundary captured by the harness for the model request.",
    )


class NewContextExecutor(ToolExecutor[NewContextAction, NewContextObservation]):
    def __call__(
        self,
        action: NewContextAction,  # noqa: ARG002
        conversation: LocalConversation | None = None,
    ) -> NewContextObservation:
        if conversation is None:
            return NewContextObservation.from_text(
                "A context reset requires a conversation.", is_error=True
            )
        condenser = conversation.agent.condenser
        if condenser is None or "new_context" not in condenser.required_tools():
            return NewContextObservation.from_text(
                "Select the agent_reset condenser to use new_context.", is_error=True
            )
        return NewContextObservation.from_text(
            "Context reset requested after this tool batch completes. This call, "
            "its handoff, and the batch results will remain available. Earlier "
            "messages and tool text can be retrieved with conversation_history."
        )


class NewContextTool(ToolDefinition[NewContextAction, NewContextObservation]):
    @classmethod
    def create(
        cls,
        conv_state: ConversationState | None = None,  # noqa: ARG003
        **params,
    ) -> Sequence[Self]:
        if params:
            raise ValueError("NewContextTool does not accept parameters")
        return [
            cls(
                description=(
                    "Start a fresh context window in this conversation when ready. "
                    "Optionally include a handoff explaining the task, progress, "
                    "constraints, remaining work, and useful references. You may "
                    "also use available file tools to save durable notes. The reset "
                    "takes effect after the entire tool batch finishes; the real "
                    "calls and results in that batch are retained. Earlier events "
                    "remain accessible through conversation_history."
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
