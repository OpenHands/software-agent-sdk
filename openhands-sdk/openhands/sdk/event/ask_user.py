"""Typed request/response event pair for the ``ask_user`` tool.

This mirrors the confirmation pause/resume lifecycle: the agent pauses the run
with an :class:`AskUserRequestEvent`, and a client (for example Agent Canvas)
answers with an :class:`AskUserResponseEvent` carrying the request's typed
``request_id``. The response shape follows MCP elicitation
(``accept``/``decline``/``cancel``) so SDK elicitation can be layered on later.

Both events are plain :class:`Event` subclasses rather than
``LLMConvertibleEvent``: the agent observes the answer through the tool's
observation, so the raw pair must stay out of the LLM view (the same way
``ConversationStateUpdateEvent`` and ``PauseEvent`` do).
"""

from pydantic import Field

from openhands.sdk.event.ask_user_schema import (
    AskUserAnswer,
    AskUserResponseAction,
    QuestionInfo,
)
from openhands.sdk.event.base import Event
from openhands.sdk.event.types import EventID, SourceType, ToolCallID


# Source recorded on a response the server synthesized rather than a client
# posting one (timeout expiry, conversation close, crash recovery).
ASK_USER_TIMEOUT_SOURCE: SourceType = "environment"


class AskUserRequestEvent(Event):
    """Emitted when the agent pauses to ask the user structured questions.

    Carries a typed ``request_id`` distinct from the tool-call id so a client
    that is not driving the conversation loop can match an answer to exactly one
    pending request.
    """

    source: SourceType = "agent"
    request_id: str = Field(
        description="Unique id for this request, distinct from the tool-call id."
    )
    questions: list[QuestionInfo] = Field(
        description="The structured questions the agent is asking."
    )
    action_id: EventID = Field(
        description="Id of the ActionEvent this request resolves."
    )
    tool_call_id: ToolCallID = Field(description="Tool-call id this request resolves.")
    tool_name: str = Field(
        default="ask_user", description="Name of the tool that raised the request."
    )
    timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Optional per-request timeout override. When set it takes precedence "
            "over the server-side default; unset defers to the server setting."
        ),
    )


class AskUserResponseEvent(Event):
    """The answer to a single :class:`AskUserRequestEvent`.

    Exactly one of the three elicitation actions is carried, each echoing the
    request's ``request_id``. ``answers`` is populated only for ``accept``.
    """

    source: SourceType = "user"
    request_id: str = Field(description="Id of the request this answers.")
    action: AskUserResponseAction = Field(
        description="One of 'accept', 'decline', or 'cancel'."
    )
    answers: dict[str, list[AskUserAnswer]] = Field(
        default_factory=dict,
        description=(
            "Answers keyed by question id; populated for 'accept' only. Each "
            "value is the list of selections for that question (one element for "
            "single-select, one or more for multi-select)."
        ),
    )
