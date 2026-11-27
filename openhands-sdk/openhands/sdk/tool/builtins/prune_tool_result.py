import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar, Self, cast

from pydantic import Field
from rich.text import Text

from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation
    from openhands.sdk.conversation.state import ConversationState


class PruneToolResultAction(Action):
    tool_call_id: str = Field(
        description="The id of the tool call whose result should be replaced."
    )
    summary: str = Field(
        description="A one-line summary to keep in place of the result."
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Prune tool result:\n", style="bold blue")
        content.append(self.summary)
        return content


class PruneToolResultObservation(Observation):
    @property
    def visualize(self) -> Text:
        return Text()


TOOL_DESCRIPTION = """Replaces a previous tool result with a one-line
summary of your choosing.

Use this when a tool call returned output you didn't intend and that is
useless for the rest of the task — for example a grep that matched thousands
of lines of build artifacts, or a file that turned out to be irrelevant.
The full output is removed from context; only your summary remains.
"""


class PruneToolResultExecutor(ToolExecutor):
    def __call__(
        self,
        action: PruneToolResultAction,
        conversation: "LocalConversation | None" = None,
    ) -> PruneToolResultObservation:
        # Deferred imports: the tool package is imported before the event package.
        from openhands.sdk.event.condenser import Condensation
        from openhands.sdk.event.llm_convertible.observation import (
            ObservationEvent,
        )

        if conversation is None:
            return PruneToolResultObservation.from_text(
                text="Error: no conversation context available."
            )

        events = conversation.state.events
        target = None
        for idx, event in enumerate(events):
            if (
                isinstance(event, ObservationEvent)
                and event.tool_call_id == action.tool_call_id
            ):
                target = (idx, event)
                break

        if target is None:
            return PruneToolResultObservation.from_text(
                text=f"Error: no tool result found for {action.tool_call_id}."
            )

        idx, observation = target
        # The public state protocol only reads events; appending needs the
        # concrete ConversationState.
        from openhands.sdk.conversation.state import ConversationState

        state = cast(ConversationState, conversation.state)
        state.append_event(
            Condensation(
                forgotten_event_ids={observation.id},
                summary=f"[pruned] {action.summary}",
                summary_offset=idx,
                llm_response_id=str(uuid.uuid4()),
            )
        )
        return PruneToolResultObservation.from_text(
            text=f"Pruned tool result {action.tool_call_id}: {action.summary}"
        )


class PruneToolResultTool(
    ToolDefinition[PruneToolResultAction, PruneToolResultObservation]
):
    """Tool that lets the agent swap a junk tool result for a one-line summary."""

    user_selectable: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        **params,  # noqa: ARG003
    ) -> Sequence[Self]:
        return [
            cls(
                description=TOOL_DESCRIPTION,
                action_type=PruneToolResultAction,
                observation_type=PruneToolResultObservation,
                annotations=ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                ),
                executor=PruneToolResultExecutor(),
            )
        ]
