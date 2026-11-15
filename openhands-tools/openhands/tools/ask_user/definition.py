"""The ``ask_user`` tool: ask the person one question, with options."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar, Self

from pydantic import BaseModel, Field, model_validator
from rich.text import Text

from openhands.sdk.event.ask_user import ASK_USER_TOOL_NAME
from openhands.sdk.tool.registry import register_tool
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


class AskUserOption(BaseModel):
    """One answer the person can pick."""

    label: str = Field(
        min_length=1,
        max_length=60,
        description="A few words naming the option, shown on its button.",
    )
    consequence: str = Field(
        default="",
        max_length=240,
        description="One sentence: what happens if the person picks it.",
    )


class AskUserAction(Action):
    """Ask the person one question, with two to four options."""

    question: str = Field(
        min_length=1,
        max_length=300,
        description="The question, verb first, in about twelve words.",
    )
    options: list[AskUserOption] = Field(
        min_length=2,
        max_length=4,
        description="Two to four options, each with what happens if it is picked.",
    )
    recommended: int | None = Field(
        default=None,
        ge=0,
        description="Index, from 0, of the option you recommend.",
    )

    @model_validator(mode="after")
    def _recommended_exists(self) -> "AskUserAction":
        if self.recommended is not None and self.recommended >= len(self.options):
            raise ValueError(
                f"recommended is {self.recommended}, but there are only "
                f"{len(self.options)} options (counted from 0)."
            )
        return self

    @property
    def visualize(self) -> Text:
        text = Text(f"Ask: {self.question}", style="bold")
        for i, option in enumerate(self.options):
            mark = " (recommended)" if i == self.recommended else ""
            text.append(f"\n  {i + 1}. {option.label}{mark}")
        return text


class AskUserObservation(Observation):
    """Confirms the question was shown; the answer comes as a later message."""

    @property
    def visualize(self) -> Text:
        return Text(self.text, style="red" if self.is_error else "dim")


_DESCRIPTION = """\
Ask the person one question, shown to them as a card with your options; they \
answer in one tap, or in their own words.

Ask when the choice is theirs to make: it changes what they are trying to \
achieve, spends their money or a lot of their time, or cannot be undone (a \
production change, a bulk delete, a message sent outside the conversation). \
When a request can be read more than one way but nothing is at stake, pick \
the likely reading, go on, and say which one you chose: do not ask.

Write the question verb first, in about twelve words. Give two to four \
options, each with one sentence on what happens if it is picked, and \
recommend one when you have a view. The person can always answer in their \
own words instead, so no "Other" option is needed. Ask one question per call \
and one call per turn; do not repeat the question in a message.

Calling this ends your turn, as finishing does: do the work that does not \
depend on the answer first, then ask. Do not wait or ask again. The answer \
arrives as the person's next message, starting with \
"[Answer to your question" when they picked on the card."""


class AskUserExecutor(ToolExecutor[AskUserAction, AskUserObservation]):
    """Nothing to do here: the call itself is the question clients show."""

    def __call__(
        self,
        action: AskUserAction,  # noqa: ARG002
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> AskUserObservation:
        return AskUserObservation.from_text(
            "The question is shown to the person. Your turn ends here; their "
            "answer will arrive as their next message."
        )


class AskUserTool(ToolDefinition[AskUserAction, AskUserObservation]):
    """Tool that asks the person a question with options and ends the turn."""

    name: ClassVar[str] = ASK_USER_TOOL_NAME
    # Given by the ask_user feature, not picked in agent settings.
    user_selectable: ClassVar[bool] = False
    ends_turn: ClassVar[bool] = True

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        **params,
    ) -> Sequence[Self]:
        if params:
            raise ValueError(
                f"AskUserTool does not accept parameters: {sorted(params)}"
            )
        return [
            cls(
                description=_DESCRIPTION,
                action_type=AskUserAction,
                observation_type=AskUserObservation,
                executor=AskUserExecutor(),
                annotations=ToolAnnotations(
                    title="Ask the person",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]


register_tool(AskUserTool.name, AskUserTool)
