"""The ``ask_user`` tool: ask structured questions and pause for the answer.

The tool never blocks. When the agent calls it, the run pauses with an
:class:`~openhands.sdk.event.AskUserRequestEvent`; the answer arrives later as an
:class:`~openhands.sdk.event.AskUserResponseEvent` posted to the conversation
endpoint, and the agent loop resolves the tool's observation from that pair. The
executor below is therefore only reached when the action is executed without a
resolved response, which the loop avoids.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar, Literal, Self

from pydantic import Field
from rich.text import Text

from openhands.sdk.event.ask_user_schema import AskUserAnswer, QuestionInfo
from openhands.sdk.llm import TextContent
from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState


ASK_USER_TOOL_NAME = "ask_user"

AskUserResolution = Literal["accept", "decline", "cancel"]


class AskUserAction(Action):
    """Ask the user one or more structured questions and pause for the answer."""

    questions: list[QuestionInfo] = Field(
        description=(
            "The questions to ask. Each question has a stable id, the question "
            "text, and (optionally) selectable options keyed by a stable id."
        )
    )
    timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Optional per-request timeout. Overrides the server-side default for "
            "this request; unset defers to the server setting (disabled by default)."
        ),
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("❓ ", style="bold cyan")
        content.append("Ask user:\n", style="bold cyan")
        for question in self.questions:
            content.append(f"  - {question.question}\n")
        return content


class AskUserObservation(Observation):
    """The user's resolution of an ``ask_user`` request."""

    resolution: AskUserResolution = Field(
        description="How the request resolved: accept, decline, or cancel."
    )
    answers: dict[str, list[AskUserAnswer]] = Field(
        default_factory=dict,
        description=(
            "Answers keyed by question id; populated for 'accept' only. Each "
            "value lists the selections for that question."
        ),
    )
    message: str = Field(
        default="", description="Human-readable summary of the resolution."
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Tool: ", style="bold")
        content.append(ASK_USER_TOOL_NAME)
        content.append("\nResult:\n", style="bold")
        content.append(self.message or self.resolution)
        return content

    @property
    def to_llm_content(self) -> Sequence[TextContent]:
        text = self.message or f"User response: {self.resolution}."
        return [TextContent(text=text)]


class AskUserExecutor(ToolExecutor):
    """Fallback executor.

    The agent loop resolves the observation from the request/response pair and
    never executes this tool directly. If it is reached (for example a raw
    ``_execute_actions`` without a pending request), return a safe ``cancel`` so
    the model can proceed instead of blocking.
    """

    def __call__(
        self,
        action: AskUserAction,  # noqa: ARG002
        conversation: "BaseConversation | None" = None,  # noqa: ARG002
    ) -> AskUserObservation:
        return AskUserObservation(
            resolution="cancel",
            message=(
                "The question could not be delivered to the user; proceed using "
                "your best judgement."
            ),
        )


ASK_USER_DESCRIPTION = """Ask the user one or more structured questions when you are \
blocked by genuine ambiguity that the code does not resolve, then pause execution \
until they answer.

Use this tool when the task cannot proceed without a decision only the user can \
make, for example:
- An architecture choice the repository does not encode (session vs JWT vs OAuth).
- A product or scope decision with materially different outcomes.
- A missing value that cannot be inferred from the workspace.

Each question carries a stable `id`, the `question` text, and optional `options` \
(each with a stable `id` and a `label`). Provide options whenever the answer is a \
choice; set `multi_select` when more than one option may apply. Do not use this \
tool for questions you can answer yourself by inspecting the repository."""


class AskUserTool(ToolDefinition[AskUserAction, AskUserObservation]):
    """Tool for asking the user structured questions and pausing for the answer."""

    name: ClassVar[str] = ASK_USER_TOOL_NAME

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        **params,
    ) -> Sequence[Self]:
        if params:
            raise ValueError("AskUserTool doesn't accept parameters")
        return [
            cls(
                description=ASK_USER_DESCRIPTION,
                action_type=AskUserAction,
                observation_type=AskUserObservation,
                executor=AskUserExecutor(),
                annotations=ToolAnnotations(
                    title=ASK_USER_TOOL_NAME,
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]
