"""Pydantic schema shared by the ``ask_user`` tool and its event pair.

Kept free of any ``openhands.sdk`` imports so both the tool package and the event
package can depend on it without an import cycle.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field


# The three-action response shape adopted from MCP elicitation
# (spec 2025-06-18): accept with answers, decline, or cancel.
AskUserResponseAction = Literal["accept", "decline", "cancel"]


class AskUserRequestError(Exception):
    """Raised when an answer cannot be matched to a single pending request."""


class QuestionOption(BaseModel):
    """One selectable answer for a :class:`QuestionInfo`."""

    id: str = Field(description="Stable option id used as the answer key.")
    label: str = Field(description="Human-readable label for display.")
    description: str | None = Field(
        default=None, description="Optional longer explanation of the option."
    )


class QuestionInfo(BaseModel):
    """A single structured question the agent wants the user to answer."""

    id: str = Field(description="Stable question id used to key the answers map.")
    question: str = Field(description="The question text shown to the user.")
    options: list[QuestionOption] = Field(
        default_factory=list,
        description="Selectable options; empty means a free-form question.",
    )
    multi_select: bool = Field(
        default=False,
        description="Whether the user may select more than one option.",
    )


class AskUserAnswer(BaseModel):
    """One selection the user made for a question.

    A question answered with ``multi_select`` carries a *list* of these; a
    single-select question carries a one-element list.
    """

    option_id: str = Field(description="Id of the selected option.")
    label: str = Field(description="Display label captured at answer time.")


def validate_ask_user_answers(
    questions: list[QuestionInfo],
    answers: dict[str, list[AskUserAnswer]],
) -> None:
    """Reject answers that name an unknown question or option.

    Partial answers are allowed (a client may answer only some questions); the
    observation simply reports the ones that were provided, so an empty
    selection list is accepted and treated as unanswered. Unknown ids, and more
    than one selection for a question that is not ``multi_select``, are client
    errors, so they raise :class:`AskUserRequestError`.
    """
    by_id = {question.id: question for question in questions}
    for question_id, selections in answers.items():
        question = by_id.get(question_id)
        if question is None:
            raise AskUserRequestError(f"Unknown question id '{question_id}'.")
        if not question.multi_select and len(selections) > 1:
            raise AskUserRequestError(
                f"Question '{question_id}' does not allow multiple selections."
            )
        if not question.options:
            continue
        known = {option.id for option in question.options}
        for selection in selections:
            if selection.option_id not in known:
                raise AskUserRequestError(
                    f"Unknown option id '{selection.option_id}' for question "
                    f"'{question_id}'."
                )


def normalize_ask_user_answers(
    answers: Mapping[str, Any] | None,
) -> dict[str, list[AskUserAnswer]]:
    """Coerce caller answers into the canonical ``{question_id: [answers]}`` form.

    Accepts a single :class:`AskUserAnswer`, a sequence of them (multi-select),
    or a plain mapping per question, so callers may pass either model instances
    or dictionary-shaped answers. Values are validated into ``AskUserAnswer``.
    """
    normalized: dict[str, list[AskUserAnswer]] = {}
    for question_id, answer in (answers or {}).items():
        if isinstance(answer, Mapping):
            items: Sequence[AskUserAnswer | Mapping[str, Any]] = [answer]
        elif isinstance(answer, AskUserAnswer):
            items = [answer]
        else:
            items = answer
        normalized[question_id] = [AskUserAnswer.model_validate(item) for item in items]
    return normalized
