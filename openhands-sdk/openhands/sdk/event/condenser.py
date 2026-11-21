from __future__ import annotations

from pydantic import Field
from rich.text import Text

from openhands.sdk.event.base import Event, LLMConvertibleEvent
from openhands.sdk.event.types import EventID, SourceType
from openhands.sdk.llm import Message, TextContent


class Condensation(Event):
    """This action indicates a condensation of the conversation history is happening."""

    forgotten_event_ids: set[EventID] = Field(
        default_factory=set,
        description="The IDs of the events that are being forgotten "
        "(removed from the `View` given to the LLM).",
    )

    summary: str | None = Field(
        default=None, description="An optional summary of the events being forgotten."
    )

    summary_offset: int | None = Field(
        default=None,
        ge=0,
        description="An optional offset to the start of the resulting view (after"
        " forgotten events have been removed) indicating where the summary should be"
        " inserted. If not provided, the summary will not be inserted into the view.",
    )
    llm_response_id: EventID = Field(
        description=(
            "Completion or Response ID of the LLM response that generated this event"
        ),
    )
    chars_before: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Total character length of the forgotten events before condensation."
        ),
    )
    chars_after: int | None = Field(
        default=None,
        ge=0,
        description="Character length of the replacement summary after condensation.",
    )
    tokens_before: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Token count of the forgotten events before condensation, when an LLM "
            "tokenizer is available."
        ),
    )
    tokens_after: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Token count of the replacement summary message after condensation, "
            "when an LLM tokenizer is available."
        ),
    )

    source: SourceType = "environment"

    @property
    def visualize(self) -> Text:
        text = Text()

        text.append("Auto Conversation Condensation Triggered.\n", style="bold")

        text.append(f"Forgetting {len(self.forgotten_event_ids)} events\n")
        if self.chars_before is not None and self.chars_after is not None:
            text.append(f"Characters: {self.chars_before} -> {self.chars_after}\n")
        if self.tokens_before is not None and self.tokens_after is not None:
            text.append(f"Tokens: {self.tokens_before} -> {self.tokens_after}\n")
        if self.summary:
            text.append("\n[Summary of Events Being Forgotten]\n", style="bold")
            text.append(f"{self.summary}\n")
        return text

    @property
    def summary_event(self) -> CondensationSummaryEvent:
        """Generates a CondensationSummaryEvent.

        Since summary events are not part of the main event store and are generated
        dynamically, this property ensures the created event has a unique and consistent
        ID based on the condensation event's ID.

        Raises:
            ValueError: If no summary is present.
        """
        if self.summary is None:
            raise ValueError("No summary present to generate CondensationSummaryEvent.")

        # Create a deterministic ID for the summary event.
        # This ID will be unique amongst all auto-generated IDs (by virtue of the
        # "-summary" suffix).
        # These events are not intended to be stored alongside regular events, but the
        # ID is still compatible with the file-based event store.
        summary_id = f"{self.id}-summary"

        return CondensationSummaryEvent(
            id=summary_id,
            summary=self.summary,
            forgotten_event_count=len(self.forgotten_event_ids),
            summary_offset=self.summary_offset,
            source=self.source,
        )

    @property
    def has_summary_metadata(self) -> bool:
        """Checks if both summary and summary_offset are present."""
        return self.summary is not None and self.summary_offset is not None

    def apply(self, events: list[LLMConvertibleEvent]) -> list[LLMConvertibleEvent]:
        """Applies the condensation to a list of events.

        This method removes events that are marked to be forgotten and returns a new
        list of events. If the summary metadata is present (both summary and offset),
        the corresponding CondensationSummaryEvent will be inserted at the specified
        offset _after_ the forgotten events have been removed.
        """
        output = [event for event in events if event.id not in self.forgotten_event_ids]
        if self.has_summary_metadata:
            assert self.summary_offset is not None
            summary_event = self.summary_event
            output.insert(self.summary_offset, summary_event)
        return output


class CondensationRequest(Event):
    """This action is used to request a condensation of the conversation history.

    Attributes:
        action (str): The action type, namely ActionType.CONDENSATION_REQUEST.
    """

    source: SourceType = "environment"

    @property
    def visualize(self) -> Text:
        text = Text()
        text.append("Conversation Condensation Requested\n", style="bold")
        message = (
            "A condensation of the conversation history has been requested to "
            "manage context window usage.\n"
        )
        text.append(message)
        return text


class CondensationSummaryEvent(LLMConvertibleEvent):
    """This event represents a summary generated by a condenser."""

    summary: str
    """The summary text."""

    forgotten_event_count: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Number of conversation events replaced by this summary, when known."
        ),
    )
    summary_offset: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Zero-based view index where the condensed span begins, when known."
        ),
    )

    source: SourceType = "environment"

    def _format_span_notice(self) -> str | None:
        count = self.forgotten_event_count
        offset = self.summary_offset
        if count is None and offset is None:
            return None
        if count is not None and offset is not None:
            noun = "event" if count == 1 else "events"
            if count > 0:
                end_idx = offset + count - 1
                span = (
                    f"view index {offset}"
                    if count == 1
                    else f"view indices {offset}..{end_idx}"
                )
                return (
                    f"[Context condensation: this summary replaces {count} earlier "
                    f"{noun} ({span}).]"
                )
            return (
                f"[Context condensation: this summary replaces 0 earlier events "
                f"at view index {offset}.]"
            )
        if count is not None:
            noun = "event" if count == 1 else "events"
            return (
                f"[Context condensation: this summary replaces {count} earlier {noun}.]"
            )
        return (
            f"[Context condensation: this summary replaces condensed earlier "
            f"events starting at view index {offset}.]"
        )

    def to_llm_message(self) -> Message:
        notice = self._format_span_notice()
        if notice is None:
            text = self.summary
        else:
            text = f"{notice}\n\n{self.summary}" if self.summary else notice
        return Message(
            role="user",
            content=[TextContent(text=text)],
        )

    def __str__(self) -> str:
        """Full summary text so multi-pass condensations retain all sections."""
        return f"{self.__class__.__name__} ({self.source})\n  {self.summary}"
