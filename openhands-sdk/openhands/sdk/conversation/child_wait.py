"""Canonical wire models for waiting on existing direct children."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from openhands.sdk.conversation.state import ConversationExecutionStatus


class WaitForChildrenRequest(BaseModel):
    conversation_ids: list[UUID] = Field(min_length=1, max_length=64)
    timeout_seconds: float = Field(default=60, ge=0, le=300, allow_inf_nan=False)
    delete_on_finish: bool = False
    on_timeout: Literal["retain", "stop", "delete"] = "retain"

    @field_validator("conversation_ids")
    @classmethod
    def unique_children(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("conversation_ids must be unique")
        return value


class ChildConversationWaitResult(BaseModel):
    conversation_id: UUID
    execution_status: ConversationExecutionStatus
    settled: bool
    deleted: bool = False
    mailbox_message_id: str | None = None


class WaitForChildrenResponse(BaseModel):
    children: list[ChildConversationWaitResult]
    timed_out: bool = False
    interrupted: bool = False
