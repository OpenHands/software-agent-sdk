from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from openhands.sdk.llm.message import ImageContent, Message, TextContent


class SendMessageRequest(BaseModel):
    """Payload to send a message to the agent."""

    role: Literal["user", "system", "assistant", "tool"] = "user"
    content: list[TextContent | ImageContent] = Field(default_factory=list)
    run: bool = Field(
        default=False,
        description="Whether the agent loop should automatically run if not running",
    )
    sender_conversation_id: UUID | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "The conversation sending this message, when an agent rather than "
            "the user sends it. The saved message records it as its `sender` "
            "(`conversation:<id hex>`) and opens with a line telling the "
            "receiving agent which conversation sent it, that the user did "
            "not, and that it approves nothing on the user's behalf. On a new "
            "conversation's `initial_message` it marks the first message the "
            "same way. Leave it out for the user's own messages."
        ),
    )

    def create_message(self) -> Message:
        return Message(role=self.role, content=self.content)
