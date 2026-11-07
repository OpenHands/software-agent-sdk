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
        description=(
            "The conversation sending this message, such as a project's "
            "coordinator. The saved message then records it as its `sender` "
            "(`conversation:<id hex>`) and starts with a line telling the "
            "receiving agent who sent it and that it approves nothing on the "
            "person's behalf. A sender in a project may only message "
            "conversations of the same project. Leave it out for the person's "
            "own messages."
        ),
    )
    refuse_pending_confirmation: bool = Field(
        default=False,
        description=(
            "With `run`, never run a conversation that is waiting for "
            "confirmation, since that run would approve its pending actions. "
            "A conversation already waiting answers 409 and nothing is saved; "
            "one that reaches a confirmation while the message is delivered "
            "keeps the message and is not run (`run: waiting_for_confirmation`)."
        ),
    )

    def create_message(self) -> Message:
        return Message(role=self.role, content=self.content)
