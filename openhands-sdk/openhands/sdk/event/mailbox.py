"""Wire payload shared by mailbox requests and persisted message events."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MailboxSendRequest(BaseModel):
    """Send one durable, idempotent message within a conversation tree."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    recipient_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=256)
    kind: Literal["question", "proposal", "review-request", "blocker", "decision"]
    body: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    delivery: Literal["steer", "interrupt"] = "steer"

    @model_validator(mode="after")
    def validate_message(self):
        if not self.body.strip() or not self.idempotency_key.strip():
            raise ValueError("body and idempotency_key must not be blank")
        if any(not item.strip() for item in self.evidence):
            raise ValueError("evidence entries must not be blank")
        if self.kind in {"proposal", "review-request", "blocker"} and not self.evidence:
            raise ValueError("evidence is required for this message kind")
        return self


class MailboxMessage(MailboxSendRequest):
    """The server-assigned identity and authenticated origin of a message."""

    message_id: str
    sender_id: UUID


class MailboxSendResponse(BaseModel):
    message_id: str
    duplicate: bool = False


class MailboxConflict(ValueError):
    """An idempotency key was reused with different message contents."""
