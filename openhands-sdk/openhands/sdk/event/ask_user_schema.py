"""Pydantic schema shared by the ``ask_user`` tool and its event pair.

Kept free of any ``openhands.sdk`` imports so both the tool package and the event
package can depend on it without an import cycle.
"""

from typing import Literal

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
    """The user's selection for one question."""

    option_id: str = Field(description="Id of the selected option.")
    label: str = Field(description="Display label captured at answer time.")
