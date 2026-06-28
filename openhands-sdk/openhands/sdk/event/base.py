import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from pydantic import ConfigDict, Field
from rich.text import Text

from openhands.sdk.event.types import EventID, SourceType
from openhands.sdk.llm import ImageContent, Message, TextContent
from openhands.sdk.utils.models import DiscriminatedUnionMixin


if TYPE_CHECKING:
    from openhands.sdk.event.llm_convertible import ActionEvent

N_CHAR_PREVIEW = 500


class Event(DiscriminatedUnionMixin, ABC):
    """Base class for all events."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    id: EventID = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique event id (ULID/UUID)",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="Event timestamp",
    )  # consistent with V1
    source: SourceType = Field(..., description="The source of this event")
    parent_tool_use_id: str | None = Field(
        default=None,
        description=(
            "If set, this event was produced inside a sub-agent; the value is the "
            "tool_call_id of the parent TaskAction that spawned it. None for "
            "main-agent events. Mirrors Anthropic's parent_tool_use_id."
        ),
    )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this event.

        This is a fallback implementation for unknown event types.
        Subclasses should override this method to provide specific visualization.
        """
        content = Text()
        content.append(f"Unknown event type: {self.__class__.__name__}")
        content.append(f"\n{self.model_dump()}")
        return content

    def __str__(self) -> str:
        """Plain text string representation for display."""
        return f"{self.__class__.__name__} ({self.source})"

    def __repr__(self) -> str:
        """Developer-friendly representation."""
        return (
            f"{self.__class__.__name__}(id='{self.id[:8]}...', "
            f"source='{self.source}', timestamp='{self.timestamp}')"
        )


class LLMConvertibleEvent(Event, ABC):
    """Base class for events that can be converted to LLM messages."""

    @abstractmethod
    def to_llm_message(self) -> Message:
        raise NotImplementedError()

    def __str__(self) -> str:
        """Plain text string representation showing LLM message content."""
        base_str = super().__str__()
        try:
            llm_message = self.to_llm_message()
            # Extract text content from the message
            text_parts = []
            for content in llm_message.content:
                if isinstance(content, TextContent):
                    text_parts.append(content.text)
                elif isinstance(content, ImageContent):
                    text_parts.append(f"[Image: {len(content.image_urls)} URLs]")

            if text_parts:
                content_preview = " ".join(text_parts)
                # Truncate long content for display
                if len(content_preview) > N_CHAR_PREVIEW:
                    content_preview = content_preview[: N_CHAR_PREVIEW - 3] + "..."
                return f"{base_str}\n  {llm_message.role}: {content_preview}"
            else:
                return f"{base_str}\n  {llm_message.role}: [no text content]"
        except Exception:
            # Fallback to base representation if LLM message conversion fails
            return base_str

    @staticmethod
    def events_to_messages(events: list["LLMConvertibleEvent"]) -> list[Message]:
        """Convert event stream to LLM message stream, handling multi-action batches"""
        # TODO: We should add extensive tests for this
        from openhands.sdk.event.llm_convertible import ActionEvent

        messages = []
        i = 0

        while i < len(events):
            event = events[i]

            if isinstance(event, ActionEvent):
                # Collect all ActionEvents from same LLM response
                # This happens when function calling happens
                batch_events: list[ActionEvent] = [event]
                response_id = event.llm_response_id

                # Look ahead for related events
                j = i + 1
                while j < len(events) and isinstance(events[j], ActionEvent):
                    event = events[j]
                    assert isinstance(event, ActionEvent)  # for type checker
                    if event.llm_response_id != response_id:
                        break
                    batch_events.append(event)
                    j += 1

                # Create combined message for the response
                msg = _combine_action_events(batch_events)
                if messages and _can_merge_user_messages(messages[-1], msg):
                    messages[-1].content = list(messages[-1].content) + list(
                        msg.content
                    )
                else:
                    messages.append(msg)
                i = j
            else:
                # Regular event - direct conversion
                msg = event.to_llm_message()
                if messages and _can_merge_user_messages(messages[-1], msg):
                    messages[-1].content = list(messages[-1].content) + list(
                        msg.content
                    )
                else:
                    messages.append(msg)
                i += 1

        return messages


def _is_plain_user_message(message: Message) -> bool:
    """A plain user turn with no tool-call metadata — safe to coalesce."""
    return (
        message.role == "user"
        and message.tool_calls is None
        and message.tool_call_id is None
        and message.name is None
    )


def _can_merge_user_messages(previous: Message, current: Message) -> bool:
    """Return whether two user messages can be safely sent as one LLM turn."""
    return _is_plain_user_message(previous) and _is_plain_user_message(current)


def _combine_action_events(events: list["ActionEvent"]) -> Message:
    """Combine multiple ActionEvents into single LLM message.

    We receive multiple ActionEvents per LLM message WHEN LLM returns
    multiple tool calls with parallel function calling.
    """
    if len(events) == 1:
        return events[0].to_llm_message()
    # Multi-action case - reconstruct original LLM response
    for e in events[1:]:
        assert len(e.thought) == 0, (
            "Expected empty thought for multi-action events after the first one"
        )

    return Message(
        role="assistant",
        content=events[0].thought,  # Shared thought content only in the first event
        tool_calls=[event.tool_call for event in events],
        reasoning_content=events[0].reasoning_content,  # Shared reasoning content
        thinking_blocks=events[0].thinking_blocks,  # Shared thinking blocks
    )
