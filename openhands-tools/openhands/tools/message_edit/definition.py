"""The ``edit_message`` tool."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar, Self

from pydantic import Field
from rich.text import Text

from openhands.sdk.event import Event, EventID, MessageEditEvent
from openhands.sdk.tool.registry import register_tool
from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


class EditMessageAction(Action):
    """Replace text in one of your earlier messages."""

    old_str: str = Field(
        description=(
            "Text from one of your earlier messages, copied exactly as the "
            "message reads now (with any earlier edit). It must occur only "
            "once across your messages; include more of the text around it if "
            "it does not."
        )
    )
    new_str: str = Field(description="The text to put in its place.")

    @property
    def visualize(self) -> Text:
        return Text(f"Edit message: {self.old_str!r} -> {self.new_str!r}", style="dim")


class EditMessageObservation(Observation):
    """The result of ``edit_message``.

    On success it carries the edit, which the agent records as a
    :class:`MessageEditEvent` right after this observation.
    """

    target_event_id: EventID | None = None
    edited_text: str | None = None

    def follow_up_events(self) -> Sequence[Event]:
        if self.is_error or self.target_event_id is None or self.edited_text is None:
            return ()
        return (
            MessageEditEvent(
                target_event_id=self.target_event_id, text=self.edited_text
            ),
        )

    @property
    def visualize(self) -> Text:
        if self.is_error:
            return Text(self.text, style="red")
        return Text()


_DESCRIPTION = """\
Edit one of your earlier messages to the user when it is out of date:
- a question you asked got answered, by the user or by what you found:
  strike the question and note the answer;
- something you said was wrong, or became wrong: strike it and note what
  holds now.
The user sees the edited message in place, marked "edited"; editing does not
notify them and does not end your turn.

Keep what you wrote visible: strike the outdated part with ~~...~~ and add a
short [Edit: ...] after it. For example, old_str
"Should I run the lookup on windy?" and new_str
"~~Should I run the lookup on windy?~~ [Edit: Running it on windy.]".
Strike each line or list item on its own, since ~~ does not span lines.

Do not edit to reword or polish. Something new for the user (a result, a
question, a change of plan) belongs in a new message, not an edit."""


class EditMessageTool(ToolDefinition[EditMessageAction, EditMessageObservation]):
    """Tool that edits one of the agent's earlier messages."""

    # Given by the message_edits feature, not picked in agent settings.
    user_selectable: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        **params,
    ) -> Sequence[Self]:
        if params:
            raise ValueError(
                f"EditMessageTool does not accept parameters: {sorted(params)}"
            )
        from openhands.tools.message_edit.impl import EditMessageExecutor

        return [
            cls(
                description=_DESCRIPTION,
                action_type=EditMessageAction,
                observation_type=EditMessageObservation,
                executor=EditMessageExecutor(),
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    # Each call records a new MessageEditEvent.
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]


register_tool(EditMessageTool.name, EditMessageTool)
