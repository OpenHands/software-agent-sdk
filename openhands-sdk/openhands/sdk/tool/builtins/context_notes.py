"""Conversation-local notes replayed from successful tool observations."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal, Self

from pydantic import Field
from rich.text import Text

from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
    from openhands.sdk.conversation.state import ConversationState
    from openhands.sdk.event import Event, ObservationEvent


class ContextNotesAction(Action):
    """Read, replace, or append to this conversation's notes."""

    command: Literal["read", "write", "append"]
    content: str | None = Field(
        default=None,
        description="Required for write/append; at most 16000 characters per call.",
    )
    version_id: str | None = Field(
        default=None, description="For read, pin a previous successful notes version."
    )
    offset: int = Field(default=0, ge=0, description="Read offset in characters.")
    limit: int = Field(default=4000, ge=1, le=8000)


class ContextNotesObservation(Observation):
    """A committed write stores its masked replacement or append delta."""

    command: Literal["read", "write", "append"]
    saved_content: str | None = None
    version_id: str | None = None
    total_chars: int | None = None
    next_offset: int | None = None

    @property
    def visualize(self) -> Text:
        return Text(self.text)


def get_context_notes(
    events: Sequence[Event], version_id: str | None = None
) -> tuple[ObservationEvent | None, str]:
    """Replay successful notes mutations on a root-first branch snapshot.

    A version is the last included mutation's ObservationEvent ID. Reads and
    failed or uncommitted actions never change notes.
    """
    from openhands.sdk.event import ObservationEvent

    pieces: list[str] = []
    latest: ObservationEvent | None = None
    for event in events:
        if not isinstance(event, ObservationEvent):
            continue
        observation = event.observation
        if (
            not isinstance(observation, ContextNotesObservation)
            or observation.is_error
            or observation.command == "read"
            or observation.saved_content is None
        ):
            continue
        if observation.command == "write":
            pieces.clear()
        pieces.append(observation.saved_content)
        latest = event
        if event.id == version_id:
            return latest, "".join(pieces)
    if version_id is not None:
        raise ValueError("Notes version is not available on the active branch.")
    return latest, "".join(pieces)


class ContextNotesExecutor(ToolExecutor[ContextNotesAction, ContextNotesObservation]):
    def __call__(
        self,
        action: ContextNotesAction,
        conversation: LocalConversation | None = None,
    ) -> ContextNotesObservation:
        if conversation is None:
            return ContextNotesObservation.from_text(
                "Notes require a conversation.", is_error=True, command=action.command
            )
        if action.command != "read":
            if action.content is None or action.version_id is not None:
                return ContextNotesObservation.from_text(
                    "write/append require content and do not accept version_id.",
                    is_error=True,
                    command=action.command,
                )
            if len(action.content) > 16000:
                return ContextNotesObservation.from_text(
                    f"Received {len(action.content)} characters; the per-call limit "
                    "is 16000. Notes were not changed. Split the content into chunks "
                    "of at most 16000 characters: write the first replacement chunk, "
                    "then append each remaining chunk. Use append for every chunk "
                    "when extending existing notes.",
                    is_error=True,
                    command=action.command,
                )
            return ContextNotesObservation.from_text(
                f"Notes {action.command} accepted ({len(action.content)} characters). "
                "This becomes a notes version when the observation is committed "
                "to the conversation event log. Read it in the next tool call.",
                command=action.command,
                saved_content=action.content,
            )
        try:
            if action.content is not None:
                raise ValueError("read does not accept content.")
            latest, notes = get_context_notes(
                conversation.state.active_branch(), action.version_id
            )
            if action.offset > len(notes):
                raise ValueError(f"offset exceeds notes length ({len(notes)}).")
        except ValueError as exc:
            return ContextNotesObservation.from_text(
                str(exc), is_error=True, command="read"
            )
        end = min(len(notes), action.offset + action.limit)
        version_id = latest.id if latest else None
        next_offset = end if end < len(notes) else None
        return ContextNotesObservation.from_text(
            json.dumps(
                {
                    "text": notes[action.offset : end],
                    "version_id": version_id,
                    "offset": action.offset,
                    "total_chars": len(notes),
                    "next_offset": next_offset,
                },
                ensure_ascii=False,
            ),
            command="read",
            version_id=version_id,
            total_chars=len(notes),
            next_offset=next_offset,
        )


class ContextNotesTool(ToolDefinition[ContextNotesAction, ContextNotesObservation]):
    """Optional SDK tool for notes that survive context condensation."""

    @classmethod
    def create(
        cls,
        conv_state: ConversationState | None = None,  # noqa: ARG003
        **params,
    ) -> Sequence[Self]:
        if params:
            raise ValueError("ContextNotesTool does not accept parameters")
        return [
            cls(
                description=(
                    "Keep working notes for this conversation's active branch. "
                    "write replaces the notes; append adds the exact supplied delta "
                    "(include any desired newline). Each mutation accepts at most "
                    "16000 characters; split longer notes into sequential appends. "
                    "read is paginated; reuse its version_id for stable subsequent "
                    "pages. Writes in one tool batch commit in call order; a read "
                    "in that same batch sees only previously committed notes. "
                    "Notes survive condensation; restart durability follows the "
                    "conversation's persistence configuration. Mutations commit "
                    "with their observation; direct execute_tool calls do not "
                    "persist them. Keep event IDs for "
                    "details available through conversation_history."
                ),
                action_type=ContextNotesAction,
                observation_type=ContextNotesObservation,
                executor=ContextNotesExecutor(),
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]
