import os
from collections.abc import Sequence

from pydantic import Field, model_validator

from openhands.sdk.context.condenser.base import RollingCondenser
from openhands.sdk.context.condenser.utils import get_total_token_count
from openhands.sdk.context.prompts import render_template
from openhands.sdk.context.view import View
from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.observability.laminar import observe


class LLMSummarizingCondenser(RollingCondenser):
    """LLM-based condenser that summarizes forgotten events.

    Uses an independent LLM for generating summaries of forgotten events. The optional
    LLM parameter passed to condense() is the LLM used by the agent, and you should not
    assume it is the same as the one defined in this condenser.
    """

    llm: LLM
    max_size: int = Field(default=120, gt=0)
    max_tokens: int | None = None
    keep_first: int = Field(default=4, ge=0)

    @model_validator(mode="after")
    def validate_keep_first_vs_max_size(self):
        events_from_tail = self.max_size // 2 - self.keep_first - 1
        if events_from_tail <= 0:
            raise ValueError(
                "keep_first must be less than max_size // 2 to leave room for "
                "condensation"
            )
        return self

    def handles_condensation_requests(self) -> bool:
        return True

    def should_condense(self, view: View, llm: LLM | None = None) -> bool:
        # Case 1: There is an unhandled condensation request. The view handles the
        # detection of these requests while processing the event stream.
        if view.unhandled_condensation_request:
            return True
        
        # Case 2: A token limit is provided and exceeded.
        if self.max_tokens and llm:
            total_tokens = get_total_token_count(view.events, llm)
            if total_tokens > self.max_tokens:
                return True

        # Case 3: The view exceeds the maximum size in number of events.
        return len(view) > self.max_size

    def _get_summary_event_content(self, view: View) -> str:
        """Extract the text content from the summary event in the view, if any.
        
        If there is no summary event or it does not contain text content, returns an
        empty string.
        """
        summary_event_content: str = ""

        summary_event = view.summary_event
        if isinstance(summary_event, MessageEvent):
            message_content = summary_event.llm_message.content[0]
            if isinstance(message_content, TextContent):
                summary_event_content = message_content.text

        return summary_event_content

    def _generate_condensation(
        self,
        summary_event_content: str,
        forgotten_events: Sequence[LLMConvertibleEvent]
    ) -> Condensation:
        """Generate a condensation by using the condenser's LLM to summarize forgotten
        events.

        Args:
            summary_event_content: The content of the previous summary event.
            forgotten_events: The list of events to be summarized.

        Returns:
            Condensation: The generated condensation object.
        """
        # Convert events to strings for the template
        event_strings = [str(forgotten_event) for forgotten_event in forgotten_events]

        prompt = render_template(
            os.path.join(os.path.dirname(__file__), "prompts"),
            "summarizing_prompt.j2",
            previous_summary=summary_event_content,
            events=event_strings,
        )

        messages = [Message(role="user", content=[TextContent(text=prompt)])]

        # Do not pass extra_body explicitly. The LLM handles forwarding
        # litellm_extra_body only when it is non-empty.
        llm_response = self.llm.completion(
            messages=messages,
        )
        # Extract summary from the LLMResponse message
        summary = None
        if llm_response.message.content:
            first_content = llm_response.message.content[0]
            if isinstance(first_content, TextContent):
                summary = first_content.text

        return Condensation(
            forgotten_event_ids=[event.id for event in forgotten_events],
            summary=summary,
            summary_offset=self.keep_first,
            llm_response_id=llm_response.id,
        )

    def _get_forgotten_events(self, view: View) -> Sequence[LLMConvertibleEvent]:
        """Identify events to be forgotten (those not in head or tail)"""
        head = view[: self.keep_first]
        target_size = self.max_size // 2
        if view.unhandled_condensation_request:
            # Condensation triggered by a condensation request
            # should be calculated based on the view size.
            target_size = len(view) // 2
        # Number of events to keep from the tail -- target size, minus however many
        # prefix events from the head, minus one for the summarization event
        events_from_tail = target_size - len(head) - 1

        # Identify events to be forgotten (those not in head or tail)
        return view[self.keep_first : -events_from_tail]

    @observe(ignore_inputs=["view", "llm"])
    def get_condensation(self, view: View, llm: LLM | None = None) -> Condensation:
        # The condensation is dependent on the events we want to drop and the previous
        # summary.
        summary_event_content = self._get_summary_event_content(view)
        forgotten_events = self._get_forgotten_events(view)

        return self._generate_condensation(
            summary_event_content=summary_event_content,
            forgotten_events=forgotten_events,
        )
