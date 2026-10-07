"""``ask_user``: ask the user structured questions and pause the run.

The agent calls ``ask_user`` with one or more questions, each offering 2-4
labelled options (plus an always-available free-text "Other" escape hatch).
The call pauses the run at ``WAITING_FOR_CONFIRMATION`` and records a pending
request; the next user message sent through ``send_message()`` that parses as an
answer for that request is converted into the tool observation, and the run
resumes with that observation in history.

This slice reuses the confirmation pause/resume shape: there is no dedicated
Agent Server endpoint or event type, and no new serialized status value. The
pending request is derived from the unmatched ``ask_user`` ``ActionEvent`` in
the event log, and the answer is matched to it by ``request_id`` (the action
event id). The typed request/response transport lands in a follow-up.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field
from rich.text import Text

from openhands.sdk.llm import TextContent
from openhands.sdk.tool.registry import register_tool
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
    from openhands.sdk.event import ActionEvent


MAX_OPTIONS = 4
MIN_OPTIONS = 2
MAX_QUESTIONS = 4
MAX_HEADER_LENGTH = 12


class OptionInfo(BaseModel):
    """One selectable answer for a question."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(
        description=(
            "Stable identifier for this option, referenced by the answer. "
            "Must be unique within the question."
        )
    )
    label: str = Field(description="Short display label for the option.")
    description: str = Field(
        description="Explanation of what choosing this option means."
    )


class QuestionInfo(BaseModel):
    """A single structured question with a fixed set of options."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    question: str = Field(description="The full question text to show the user.")
    header: str = Field(
        description=(
            "Short label (at most 12 characters) used to key the answer and to "
            "render a compact heading."
        )
    )
    options: list[OptionInfo] = Field(
        description=f"Between {MIN_OPTIONS} and {MAX_OPTIONS} selectable options."
    )
    multi_select: bool = Field(
        default=False,
        alias="multiSelect",
        description="When true the user may choose more than one option.",
    )


class AskUserAction(Action):
    """Action for asking the user one or more structured questions."""

    questions: list[QuestionInfo] = Field(
        description="The questions to ask. Each is answered in the same call."
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Ask user:\n", style="bold cyan")
        for i, q in enumerate(self.questions):
            if i:
                content.append("\n")
            content.append(f"  {q.header}: ", style="bold")
            content.append(q.question)
            content.append("\n")
            for opt in q.options:
                content.append(f"    - {opt.label}: {opt.description}\n", style="dim")
        return content


class AskUserAnswerAction(str, Enum):
    """How the user resolved an ``ask_user`` request."""

    ACCEPT = "accept"
    DECLINE = "decline"
    CANCEL = "cancel"


class SelectedOption(BaseModel):
    """One option selected by the user, or a free-text "Other" value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    option_id: str | None = Field(
        default=None,
        description=(
            "Id of the chosen option, or null when the user typed free text "
            "instead of picking a listed option."
        ),
    )
    label: str = Field(
        description=(
            "Display label for the choice; the free text when option_id is null."
        )
    )


class QuestionAnswer(BaseModel):
    """The user's answer to a single question."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(description="The question this answer responds to.")
    header: str = Field(description="The question's short header.")
    selected: list[SelectedOption] = Field(
        default_factory=list, description="The options the user selected."
    )


class AskUserObservation(Observation):
    """Observation carrying the user's answers to an ``ask_user`` request."""

    request_id: str = Field(
        default="",
        description="Id of the ``ask_user`` request these answers respond to.",
    )
    action: AskUserAnswerAction = Field(
        default=AskUserAnswerAction.CANCEL,
        description="How the user resolved the request.",
    )
    answers: list[QuestionAnswer] = Field(
        default_factory=list, description="The answers, one per answered question."
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        if self.is_error:
            content.append("ask_user failed\n", style="bold red")
            content.append(self.text)
            return content
        if self.action == AskUserAnswerAction.DECLINE:
            content.append("User declined to answer.", style="bold yellow")
            return content
        if self.action == AskUserAnswerAction.CANCEL:
            content.append("User dismissed the question.", style="bold yellow")
            return content
        content.append("User answered:\n", style="bold green")
        for answer in self.answers:
            labels = ", ".join(opt.label for opt in answer.selected) or "(no selection)"
            content.append(f"  {answer.header}: ", style="bold")
            content.append(labels)
            content.append("\n")
        return content


# ---------------------------------------------------------------------------
# Wire format for the answer carried in a user message
# ---------------------------------------------------------------------------


class AskUserSelection(BaseModel):
    """One selection in the answer payload carried by a user message."""

    model_config = ConfigDict(extra="ignore")

    option_id: str | None = None
    label: str | None = None
    other_text: str | None = None


class AskUserAnswerPayload(BaseModel):
    """The structured answer a client sends to resolve an ``ask_user`` request.

    Carried as a JSON object in the user message text::

        {"request_id": "<action event id>",
         "action": "accept" | "decline" | "cancel",
         "answers": {"<header>": [{"option_id": "jwt", "label": "JWT"}]}}

    A free-text "Other" value is carried with ``option_id`` null and the text in
    ``label`` (or ``other_text``).
    """

    model_config = ConfigDict(extra="ignore")

    request_id: str
    action: AskUserAnswerAction = AskUserAnswerAction.ACCEPT
    answers: dict[str, list[AskUserSelection]] = Field(default_factory=dict)


def parse_ask_user_answer(text: str) -> AskUserAnswerPayload | None:
    """Parse ``text`` as an answer payload, or return None if it is not one.

    Returns None for anything that is not a JSON object carrying a
    ``request_id`` string, so an ordinary user message is left to the normal
    user-turn path.
    """
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("request_id"), str):
        return None
    try:
        return AskUserAnswerPayload.model_validate(data)
    except ValueError:
        return None


def _resolve_selected(
    selection: AskUserSelection, question: QuestionInfo
) -> tuple[SelectedOption | None, str | None]:
    """Resolve one wire selection against a question, or return an error."""
    if selection.option_id is None:
        free_text = selection.label or selection.other_text
        if not free_text:
            return None, (
                f"answer for '{question.header}' has no option_id and no free text"
            )
        return SelectedOption(option_id=None, label=free_text), None
    match = next((o for o in question.options if o.id == selection.option_id), None)
    if match is None:
        valid = ", ".join(o.id for o in question.options)
        return None, (
            f"unrecognized option id '{selection.option_id}' for "
            f"'{question.header}' (valid: {valid})"
        )
    return SelectedOption(
        option_id=match.id, label=selection.label or match.label
    ), None


def build_ask_user_observation(
    pending_action: ActionEvent, payload: AskUserAnswerPayload
) -> tuple[AskUserObservation | None, str | None]:
    """Convert an answer payload into an observation for the pending request.

    Returns ``(observation, None)`` on success, or ``(None, error)`` when the
    answer is mis-keyed or references an unknown option. The caller leaves the
    request pending on error so it can be answered again.
    """
    if payload.request_id != pending_action.id:
        return None, (
            f"answer request_id '{payload.request_id}' does not match the pending "
            f"ask_user request '{pending_action.id}'"
        )

    action = pending_action.action
    assert isinstance(action, AskUserAction), (
        "pending ask_user action must be an AskUserAction"
    )

    if payload.action != AskUserAnswerAction.ACCEPT:
        return (
            AskUserObservation(
                request_id=pending_action.id,
                action=payload.action,
                answers=[],
                content=[TextContent(text=_resolution_text(payload.action, []))],
            ),
            None,
        )

    questions_by_header = {q.header: q for q in action.questions}
    answers: list[QuestionAnswer] = []
    for header, selections in payload.answers.items():
        question = questions_by_header.get(header)
        if question is None:
            valid = ", ".join(questions_by_header)
            return None, (f"unrecognized question header '{header}' (valid: {valid})")
        if not question.multi_select and len(selections) > 1:
            return None, (
                f"question '{header}' is single-select but {len(selections)} "
                "options were chosen"
            )
        selected: list[SelectedOption] = []
        for selection in selections:
            resolved, error = _resolve_selected(selection, question)
            if error is not None:
                return None, error
            assert resolved is not None
            selected.append(resolved)
        answers.append(
            QuestionAnswer(question=question.question, header=header, selected=selected)
        )

    return (
        AskUserObservation(
            request_id=pending_action.id,
            action=AskUserAnswerAction.ACCEPT,
            answers=answers,
            content=[TextContent(text=_resolution_text(payload.action, answers))],
        ),
        None,
    )


def _resolution_text(action: AskUserAnswerAction, answers: list[QuestionAnswer]) -> str:
    if action == AskUserAnswerAction.DECLINE:
        return "The user declined to answer and asked you to stop asking."
    if action == AskUserAnswerAction.CANCEL:
        return "The user dismissed the question without answering."
    if not answers:
        return "The user accepted the request but provided no answers."
    lines = ["The user answered:"]
    for answer in answers:
        labels = ", ".join(opt.label for opt in answer.selected) or "(no selection)"
        lines.append(f"- {answer.header}: {labels}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


def validate_questions(action: AskUserAction) -> str | None:
    """Validate an ``ask_user`` call, returning a corrective message or None."""
    questions = action.questions
    if not questions:
        return "ask_user requires at least one question."
    if len(questions) > MAX_QUESTIONS:
        return (
            f"ask_user accepts at most {MAX_QUESTIONS} questions per call "
            f"(got {len(questions)}); split them across multiple calls."
        )
    headers: set[str] = set()
    for q in questions:
        if not q.question.strip():
            return "every ask_user question needs non-empty question text."
        header = q.header.strip()
        if not header:
            return "every ask_user question needs a non-empty header."
        if len(header) > MAX_HEADER_LENGTH:
            return f"header '{header}' is longer than {MAX_HEADER_LENGTH} characters."
        if header in headers:
            return f"duplicate question header '{header}'; headers must be unique."
        headers.add(header)
        if not MIN_OPTIONS <= len(q.options) <= MAX_OPTIONS:
            return (
                f"question '{header}' must have between {MIN_OPTIONS} and "
                f"{MAX_OPTIONS} options (got {len(q.options)})."
            )
        ids: set[str] = set()
        labels: set[str] = set()
        for opt in q.options:
            if not opt.id.strip():
                return f"question '{header}' has an option with an empty id."
            if not opt.label.strip():
                return f"question '{header}' has an option with an empty label."
            if opt.id in ids:
                return f"question '{header}' has duplicate option id '{opt.id}'."
            if opt.label in labels:
                return f"question '{header}' has duplicate option label '{opt.label}'."
            ids.add(opt.id)
            labels.add(opt.label)
    return None


class AskUserExecutor(ToolExecutor[AskUserAction, AskUserObservation]):
    """Executor for ``ask_user``.

    A valid call never reaches this executor: the agent loop pauses the run and
    converts the matching user answer into the observation. This runs only if a
    pending call is executed directly (for example, a user message that is not an
    answer implicitly confirms the batch), in which case it returns a corrective
    error observation instead of hanging.
    """

    def __call__(
        self,
        action: AskUserAction,  # noqa: ARG002
        conversation: BaseConversation | None = None,  # noqa: ARG002
    ) -> AskUserObservation:
        return AskUserObservation(
            action=AskUserAnswerAction.CANCEL,
            answers=[],
            is_error=True,
            content=[
                TextContent(
                    text=(
                        "ask_user was executed without an answer. A structured "
                        "answer must be sent through send_message() to resolve it."
                    )
                )
            ],
        )


_DESCRIPTION = """\
Ask the user one or more structured questions and pause until answered.

Use this when you hit genuine ambiguity that the code or task does not resolve and
guessing would risk wasted work, for example an architecture decision, a destructive
migration with several valid paths, or a product decision.

Each question has a short `header` (at most 12 characters), 2-4 `options` (each with a
stable `id`, a `label`, and a `description`), and a `multiSelect` flag. The user may
always answer with free text ("Other") instead of a listed option.

Calling this tool ends the current run and waits for the user. The user's answer becomes
the tool result and the run resumes. Prefer calling it with a single, well-scoped set of
questions rather than repeatedly."""


class AskUserTool(ToolDefinition[AskUserAction, AskUserObservation]):
    """Built-in tool for asking the user structured questions mid-task."""

    user_selectable: ClassVar[bool] = False

    # Signals the agent loop to pause the run instead of executing this tool.
    pauses_run_for_user_input: ClassVar[bool] = True

    catalog_description: ClassVar[str] = (
        "Ask the user structured questions and pause until answered."
    )

    def pause_error_for(self, action: Action) -> str | None:
        assert isinstance(action, AskUserAction), (
            "AskUserTool.pause_error_for expects an AskUserAction"
        )
        return validate_questions(action)

    @classmethod
    def create(
        cls,
        conv_state: ConversationState | None = None,  # noqa: ARG003
        **params: Any,
    ) -> Sequence[Self]:
        if params:
            raise ValueError("AskUserTool doesn't accept parameters")
        return [
            cls(
                description=_DESCRIPTION,
                action_type=AskUserAction,
                observation_type=AskUserObservation,
                executor=AskUserExecutor(),
                annotations=ToolAnnotations(
                    title="ask_user",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]


# Automatically register when this module is imported.
register_tool(AskUserTool.name, AskUserTool)
