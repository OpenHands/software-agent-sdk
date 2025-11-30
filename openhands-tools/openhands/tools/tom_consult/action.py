"""Action schema for Tom consultation."""

from pydantic import Field

from openhands.sdk.tool import Action


class ConsultTomAction(Action):
    """Action to consult Tom agent for guidance."""

    reason: str = Field(
        description="Brief explanation of why you need Tom agent consultation"
    )
    use_user_message: bool = Field(
        default=True,
        description=(
            "Whether to consult about the user message (True) "
            "or provide custom query (False)"
        ),
    )
    custom_query: str | None = Field(
        default=None,
        description=(
            "Custom query to ask Tom agent (only used when use_user_message is False)"
        ),
    )


class SleeptimeComputeAction(Action):
    """Action to index existing conversations for Tom's user modeling.

    This triggers Tom agent's sleeptime_compute function which processes
    conversation history to build and update the user model.
    """

    pass
