import os

from pydantic import Field, model_validator

from openhands.sdk.context.condenser.base import CondenserBase, RollingCondenser
from openhands.sdk.context.prompts import render_template
from openhands.sdk.context.view import View
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.observability.laminar import observe


class LLMSummarizingCondenser(RollingCondenser):
    llm: LLM
    max_size: int = Field(default=120, gt=0)
    keep_first: int = Field(default=4, ge=0)
    token_margin_ratio: float = Field(
        default=0.1, ge=0.0, le=0.5
    )  # reserve headroom of context window

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

    def should_condense(self, view: View) -> bool:
        if view.unhandled_condensation_request:
            return True

        # Prefer token-aware check when LLM has context window info and
        # we can estimate message tokens. Fallback to event-count otherwise.
        try:
            budget = CondenserBase.compute_token_budget(
                self.llm, self.token_margin_ratio
            )
            if budget is not None:
                total_tokens = CondenserBase.estimate_token_count(self.llm, view.events)
                return total_tokens > budget
        except Exception:
            # Any failure falls back to count-based behavior
            pass

        return len(view) > self.max_size

    @observe(ignore_inputs=["view"])
    def get_condensation(self, view: View) -> Condensation:
        head = view[: self.keep_first]

        # Prefer token-aware trimming if we have model limits; otherwise
        # fall back to event-count based trimming as before.
        events_from_tail: int | None = None
        try:
            budget = CondenserBase.compute_token_budget(
                self.llm, self.token_margin_ratio
            )
            if budget is not None:
                # Binary search the max tail we can keep within budget
                events_from_tail = CondenserBase.max_tail_within_budget(
                    view=view, llm=self.llm, keep_first=self.keep_first, budget=budget
                )
        except Exception:
            events_from_tail = None

        if events_from_tail is None:
            target_size = self.max_size // 2
            if view.unhandled_condensation_request:
                # Condensation triggered by a condensation request
                # should be calculated based on the view size.
                target_size = len(view) // 2
            # Number of events to keep from the tail -- target size, minus however many
            # prefix events from the head, minus one for the summarization event
            events_from_tail = max(0, target_size - len(head) - 1)

        summary_event_content: str = ""

        summary_event = view.summary_event
        if isinstance(summary_event, MessageEvent):
            message_content = summary_event.llm_message.content[0]
            if isinstance(message_content, TextContent):
                summary_event_content = message_content.text

        # Identify events to be forgotten (those not in head or tail)
        forgotten_events = (
            view[self.keep_first : -events_from_tail]
            if events_from_tail > 0
            else view[self.keep_first :]
        )

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
