from openhands.sdk.event.types import SourceType
from pydantic import Field
from openhands.sdk.event.base import Event

class TokenEvent(Event):
    """Event representing token IDs used in LLM interaction."""
    source: SourceType
    prompt_token_ids: list[int] = Field(
        ..., description="The exact prompt token IDs for this message event"
    )
    response_token_ids: list[int] = Field(
        ..., description="The exact response token IDs for this message event"
    )