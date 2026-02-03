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
        """Convert event stream to LLM message stream, handling multi-action batches.

        This method handles special cases:
        - ActionEvents from the same LLM response are combined into one message
        - SystemPromptEvents with dynamic_context: the dynamic context is merged
          into the first subsequent user message to avoid consecutive user messages
          while enabling cross-conversation prompt caching
        """
        # TODO: We should add extensive tests for this
        from openhands.sdk.event.llm_convertible import (
            ActionEvent,
            MessageEvent,
            SystemPromptEvent,
        )

        messages: list[Message] = []
        pending_dynamic_context: TextContent | None = None
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
                messages.append(_combine_action_events(batch_events))
                i = j
            elif isinstance(event, SystemPromptEvent):
                # Add only the system message (static prompt, cacheable)
                messages.append(event.to_llm_message())
                # Store dynamic context to merge with next user message
                if event.dynamic_context:
                    pending_dynamic_context = event.dynamic_context
                i += 1
            elif isinstance(event, MessageEvent) and event.source == "user":
                # User message - merge with pending dynamic context if present
                user_msg = event.to_llm_message()
                if pending_dynamic_context:
                    # Prepend dynamic context to user message content
                    user_msg = Message(
                        role="user",
                        content=[pending_dynamic_context] + list(user_msg.content),
                        tool_calls=user_msg.tool_calls,
                        tool_call_id=user_msg.tool_call_id,
                        name=user_msg.name,
                    )
                    pending_dynamic_context = None
                messages.append(user_msg)
                i += 1
            else:
                # Regular event - direct conversion
                messages.append(event.to_llm_message())
                i += 1

        # If there's pending dynamic context but no user message followed,
        # add it as a separate user message (edge case)
        if pending_dynamic_context:
            messages.append(Message(role="user", content=[pending_dynamic_context]))

        return messages


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
