"""The ``ask_user`` tool: ask the user one question, with options."""

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
    """One answer the user can pick."""

    label: str = Field(
        min_length=1,
        max_length=60,
        description="A few words naming the option, shown on its button.",
    )
    consequence: str = Field(
        default="",
        max_length=240,
        description="One sentence: what happens if the user picks it.",
    )


class AskUserAction(Action):
    """Ask the user one question, with two to four options."""

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


class AskUserForConversationAction(AskUserAction):
    """A coordinator's question, which may pass on a member's question."""

    for_conversation: str | None = Field(
        default=None,
        description=(
            "The id of the conversation in your project whose question you "
            "pass on to the user. Leave it empty for a question of your own."
        ),
    )


class AskUserObservation(Observation):
    """Confirms the question was shown; the answer comes as a later message."""

    @property
    def visualize(self) -> Text:
        return Text(self.text, style="red" if self.is_error else "dim")


_DESCRIPTION = """\
Ask the user one question, shown to them as a card with your options; they \
answer in one tap, or in their own words.

Ask when the choice is theirs to make: it changes what they are trying to \
achieve, spends their money or a lot of their time, or cannot be undone (a \
production change, a bulk delete, a message sent outside the conversation). \
When a request can be read more than one way but nothing is at stake, pick \
the likely reading, go on, and say which one you chose: do not ask.

Write the question verb first, in about twelve words. Give two to four \
options, each with one sentence on what happens if it is picked, and \
recommend one when you have a view. The user can always answer in their \
own words instead, so no "Other" option is needed. Ask one question per call \
and one call per turn; do not repeat the question in a message.

Calling this ends your turn, as finishing does: do the work that does not \
depend on the answer first, then ask. Do not wait or ask again. The answer \
arrives as the user's next message, starting with \
"[Answer to your question" when they picked on the card."""

_FOR_CONVERSATION_DESCRIPTION = """

A conversation in your project can ask the user a question too; you get it \
in an update, and the user answers it here, in your conversation. Pass it on \
with this tool: put that conversation's id in `for_conversation`, and keep \
its meaning and its options (you may word them more clearly or add what the \
user needs to know). However the user answers, by a tap on the card or in \
their own words in the chat, the answer reaches that conversation as theirs \
and it goes on by itself. You see the answer too: do not send it there, and \
never answer such a question yourself."""


class AskUserExecutor(ToolExecutor[AskUserAction, AskUserObservation]):
    """Nothing to do here: the call itself is the question clients show."""

    def __call__(
        self,
        action: AskUserAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> AskUserObservation:
        if any(not option.label.strip() for option in action.options):
            return AskUserObservation.from_text(
                "Each option needs a nonblank label. Ask again with named options.",
                is_error=True,
            )
        return AskUserObservation.from_text(
            "The question is shown to the user. Your turn ends here; their "
            "answer will arrive as their next message."
        )


class AskUserTool(ToolDefinition[AskUserAction, AskUserObservation]):
    """Tool that asks the user a question with options and ends the turn."""

    name: ClassVar[str] = ASK_USER_TOOL_NAME
    # Given by the ask_user feature, not picked in agent settings.
    user_selectable: ClassVar[bool] = False
    ends_turn: ClassVar[bool] = True

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        for_conversations: bool = False,
        **params,
    ) -> Sequence[Self]:
        """Make the tool.

        Args:
            for_conversations: Let the agent pass on other conversations'
                questions (``for_conversation``). A project's coordinator gets
                this, so the user answers its members' questions in one place.
        """
        if params:
            raise ValueError(
                f"AskUserTool does not accept parameters: {sorted(params)}"
            )
        return [
            cls(
                description=_DESCRIPTION
                + (_FOR_CONVERSATION_DESCRIPTION if for_conversations else ""),
                action_type=(
                    AskUserForConversationAction if for_conversations else AskUserAction
                ),
                observation_type=AskUserObservation,
                executor=AskUserExecutor(),
                annotations=ToolAnnotations(
                    title="Ask the user",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]


register_tool(AskUserTool.name, AskUserTool)
