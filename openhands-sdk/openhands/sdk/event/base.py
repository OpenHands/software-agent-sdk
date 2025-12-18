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

        This method also enforces tool call ordering to ensure that tool_result
        messages immediately follow their corresponding tool_use messages. This is
        required by providers like Anthropic.
        """
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
                messages.append(_combine_action_events(batch_events))
                i = j
            else:
                # Regular event - direct conversion
                messages.append(event.to_llm_message())
                i += 1

        # Enforce tool call ordering: tool_results must immediately follow tool_use
        return _enforce_tool_call_ordering(messages)


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


def _enforce_tool_call_ordering(messages: list[Message]) -> list[Message]:
    """Enforce that tool_result messages immediately follow their tool_use messages.

    LLM providers like Anthropic require that each tool_use block has a corresponding
    tool_result block in the immediately following message. This function reorders
    messages to ensure this constraint is satisfied.

    The algorithm:
    1. First pass: find all tool_call_ids that have matching assistant messages
    2. Second pass: for each message:
       - If assistant with tool_calls: add it, then add matching tool messages
       - If tool message with matching assistant: skip (handled with assistant)
       - If tool message without matching assistant: add as-is (orphaned)
       - Otherwise: add the message

    Note: This function does NOT filter out unmatched tool calls. That should be
    done by View.filter_unmatched_tool_calls before calling events_to_messages.
    This function only ensures proper ordering.

    Args:
        messages: List of messages that may have ordering issues

    Returns:
        List of messages with proper tool call ordering
    """
    if not messages:
        return messages

    # First pass: find tool_call_ids that have matching assistant messages
    tool_call_ids_with_assistant: set[str] = set()
    for msg in messages:
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_call_ids_with_assistant.add(tc.id)

    # Index all tool messages by their tool_call_id
    tool_messages_by_id: dict[str, Message] = {}
    for msg in messages:
        if msg.role == "tool" and msg.tool_call_id:
            tool_messages_by_id[msg.tool_call_id] = msg

    # Track which tool messages we've used (to avoid duplicates)
    used_tool_call_ids: set[str] = set()
    result: list[Message] = []

    for msg in messages:
        if msg.role == "tool" and msg.tool_call_id:
            # Check if this tool message has a matching assistant message
            if msg.tool_call_id in tool_call_ids_with_assistant:
                # Skip - will be added after its assistant message
                continue
            else:
                # Orphaned tool message - add as-is
                result.append(msg)
                continue

        if msg.role == "assistant" and msg.tool_calls:
            # This is a tool_use message - find its tool_results
            tool_call_ids = [tc.id for tc in msg.tool_calls]

            # Add assistant message
            result.append(msg)

            # Add all matching tool messages immediately after (preserving order)
            for tc_id in tool_call_ids:
                if tc_id in tool_messages_by_id:
                    result.append(tool_messages_by_id[tc_id])
                    used_tool_call_ids.add(tc_id)
        else:
            # Regular message - just append
            result.append(msg)

    return result
