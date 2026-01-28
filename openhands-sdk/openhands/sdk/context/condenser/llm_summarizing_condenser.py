import os
from collections.abc import Sequence
from enum import Enum
from logging import getLogger

from pydantic import Field, model_validator

from openhands.sdk.context.condenser.base import (
    CondensationRequirement,
    NoCondensationAvailableException,
    RollingCondenser,
)
from openhands.sdk.context.condenser.utils import (
    get_suffix_length_for_token_reduction,
    get_total_token_count,
)
from openhands.sdk.context.prompts import render_template
from openhands.sdk.context.view import View
from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.observability.laminar import observe


logger = getLogger(__name__)


class Reason(Enum):
    """Reasons for condensation."""

    REQUEST = "request"
    TOKENS = "tokens"
    EVENTS = "events"


class LLMSummarizingCondenser(RollingCondenser):
    """LLM-based condenser that summarizes forgotten events.

    Uses an independent LLM (stored in the `llm` attribute) for generating summaries
    of forgotten events. The optional `agent_llm` parameter passed to condense() is
    the LLM used by the agent for token counting purposes, and you should not assume
    it is the same as the one defined in this condenser.
    """

    llm: LLM
    max_size: int = Field(default=240, gt=0)
    max_tokens: int | None = None

    keep_first: int = Field(default=2, ge=0)
    """Minimum number of events to preserve at the start of the view. The first
    `keep_first` events in the conversation will never be condensed or summarized.
    """

    hard_context_reset_max_retries: int = Field(default=5, gt=0)
    """Number of attempts to perform hard context reset before raising an error."""

    hard_context_reset_context_scaling: float = Field(default=0.8, gt=0.0, lt=1.0)
    """When performing hard context reset, if the summarization fails, reduce the max
    size of each event string by this factor and retry.
    """

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

    def get_condensation_reasons(
        self, view: View, agent_llm: LLM | None = None
    ) -> set[Reason]:
        """Determine the reasons why the view should be condensed.

        Args:
            view: The current view to evaluate.
            agent_llm: The LLM used by the agent. Required if token counting is needed.

        Returns:
            A set of Reason enums indicating why condensation is needed.
        """
        reasons = set()

        # Reason 1: Unhandled condensation request. The view handles the detection of
        # these requests while processing the event stream.
        if view.unhandled_condensation_request:
            reasons.add(Reason.REQUEST)

        # Reason 2: Token limit is provided and exceeded.
        if self.max_tokens and agent_llm:
            total_tokens = get_total_token_count(view.events, agent_llm)
            if total_tokens > self.max_tokens:
                reasons.add(Reason.TOKENS)

        # Reason 3: View exceeds maximum size in number of events.
        if len(view) > self.max_size:
            reasons.add(Reason.EVENTS)

        return reasons

    def condensation_requirement(
        self, view: View, agent_llm: LLM | None = None
    ) -> CondensationRequirement | None:
        reasons = self.get_condensation_reasons(view, agent_llm)

        # No reasons => no condensation needed.
        if reasons == set():
            return None

        # If the reasons are for resource constraints, we can treat it as a soft
        # requirement. We want to condense when we can, but there's still space in the
        # context window or we'd also see Reason.REQUEST. That means we can delay the
        # condensation if there isn't one available (based on the view's manipulation
        # indices).
        resource_reasons = {Reason.TOKENS, Reason.EVENTS}
        if reasons.issubset(resource_reasons):
            return CondensationRequirement.SOFT

        # Requests -- whether they come from the user or the agent -- are always hard
        # requirements. We need to condense now because:
        # 1. the user expects it
        # 2. the agent has no more room in the context window and can't continue
        if Reason.REQUEST in reasons:
            return CondensationRequirement.HARD

    @staticmethod
    def _event_to_string(
        event: LLMConvertibleEvent,
        max_event_str_length: int | None = None,
        truncation_marker: str = "<TRUNCATED CONTENT>",
    ) -> str:
        """Convert an event to string, applying truncation if needed.

        Args:
            event: The event to convert.
            max_event_str_length: Optional maximum length for the event string. If
                provided, event strings longer than this will be truncated.
            truncation_marker: The marker to indicate truncated content.
        Returns:
            str: The string representation of the event, possibly truncated.
        """
        if max_event_str_length is None:
            return str(event)

        output = str(event)

        if len(output) <= max_event_str_length:
            return output

        # Cut out the middle part of the string representation to ensure the size is
        # within limits, while preserving the start and end of the event.
        half_length = (max_event_str_length - len(truncation_marker)) // 2
        return output[:half_length] + truncation_marker + output[-half_length:]

    def _generate_condensation(
        self,
        forgotten_events: Sequence[LLMConvertibleEvent],
        summary_offset: int,
        max_event_str_length: int | None = None,
    ) -> Condensation:
        """Generate a condensation by using the condenser's LLM to summarize forgotten
        events.

        Args:
            forgotten_events: The list of events to be summarized.
            summary_offset: The index where the summary event should be inserted.
            max_event_str_length: Optional maximum length for each event string. If
                provided, event strings longer than this will be truncated.

        Returns:
            Condensation: The generated condensation object.

        Raises:
            ValueError: If forgotten_events is empty (0 events to condense).
        """
        assert len(forgotten_events) > 0, "No events to condense."

        # Convert events to strings for the template
        event_strings = [
            self._event_to_string(
                forgotten_event, max_event_str_length=max_event_str_length
            )
            for forgotten_event in forgotten_events
        ]

        prompt = render_template(
            os.path.join(os.path.dirname(__file__), "prompts"),
            "summarizing_prompt.j2",
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
            summary_offset=summary_offset,
            llm_response_id=llm_response.id,
        )

    def _get_forgotten_events(
        self, view: View, agent_llm: LLM | None = None
    ) -> tuple[Sequence[LLMConvertibleEvent], int]:
        """Identify events to be forgotten and the summary offset.

        Relies on the condensation reasons to determine how many events we need to drop
        in order to maintain our resource constraints. Uses manipulation indices to
        ensure forgetting ranges respect atomic unit boundaries.

        Args:
            view: The current view from which to identify forgotten events.
            agent_llm: The LLM used by the agent, required for token-based calculations.

        Returns:
            A tuple of (events to forget, summary_offset).
        """
        reasons = self.get_condensation_reasons(view, agent_llm=agent_llm)
        assert reasons != set(), "No condensation reasons found."

        suffix_events_to_keep: set[int] = set()

        if Reason.REQUEST in reasons:
            target_size = len(view) // 2
            suffix_events_to_keep.add(target_size - self.keep_first - 1)

        if Reason.EVENTS in reasons:
            target_size = self.max_size // 2
            suffix_events_to_keep.add(target_size - self.keep_first - 1)

        if Reason.TOKENS in reasons:
            # Compute the number of tokens we need to eliminate to be under half the
            # max_tokens value. We know max_tokens and the agent LLM are not None here
            # because we can't have Reason.TOKENS without them.
            assert self.max_tokens is not None
            assert agent_llm is not None

            total_tokens = get_total_token_count(view.events, agent_llm)
            tokens_to_reduce = total_tokens - (self.max_tokens // 2)

            suffix_events_to_keep.add(
                get_suffix_length_for_token_reduction(
                    events=view.events[self.keep_first :],
                    llm=agent_llm,
                    token_reduction=tokens_to_reduce,
                )
            )

        # We might have multiple reasons to condense, so pick the strictest condensation
        # to ensure all resource constraints are met.
        events_from_tail = min(suffix_events_to_keep)

        # Calculate naive forgetting end (without considering atomic boundaries)
        naive_end = len(view) - events_from_tail

        # Find actual forgetting_start: smallest manipulation index >= keep_first
        forgetting_start = view.find_next_manipulation_index(self.keep_first)

        # Find actual forgetting_end: smallest manipulation index >= naive_end
        forgetting_end = view.find_next_manipulation_index(naive_end)

        # Extract events to forget using boundary-aware indices
        forgotten_events = view[forgetting_start:forgetting_end]

        # Summary offset is the same as forgetting_start
        return forgotten_events, forgetting_start

    @observe(ignore_inputs=["view", "agent_llm"])
    def hard_context_reset(
        self,
        view: View,
        agent_llm: LLM | None = None,  # noqa: ARG002
    ) -> Condensation | None:
        """Perform a hard context reset by summarizing all events in the view.

        Depending on how the hard context reset is triggered, this may fail (e.g., if
        the view is too large for the summarizing LLM to handle). In that case, we keep
        trimming down the contents until a summary can be generated.
        """
        max_event_str_length: int | None = None
        attempts_remaining: int = self.hard_context_reset_max_retries

        while attempts_remaining > 0:
            try:
                return self._generate_condensation(
                    forgotten_events=view.events,
                    summary_offset=0,
                    max_event_str_length=max_event_str_length,
                )
            except Exception as e:
                # If we haven't set a max_event_str_length yet, set it as the largest
                # event string length.
                if max_event_str_length is None:
                    max_event_str_length = max(len(str(event)) for event in view.events)

                # Since the summarization failed, reduce the max_event_str_length by 20%
                assert max_event_str_length is not None
                max_event_str_length = int(
                    max_event_str_length * self.hard_context_reset_context_scaling
                )

                # Log the exception so we can track these failures
                logger.warning(
                    f"Hard context reset summarization failed with exception: {e}. "
                    f"Reducing max event size to {max_event_str_length} and retrying."
                )

            attempts_remaining -= 1

        logger.error("Hard context reset summarization failed after multiple attempts.")
        return None

    @observe(ignore_inputs=["view", "agent_llm"])
    def get_condensation(
        self, view: View, agent_llm: LLM | None = None
    ) -> Condensation:
        # The condensation is dependent on the events we want to drop and the previous
        # summary.
        forgotten_events, summary_offset = self._get_forgotten_events(
            view, agent_llm=agent_llm
        )

        if not forgotten_events:
            raise NoCondensationAvailableException(
                "Cannot condense 0 events. This typically occurs when a tool loop "
                "spans almost the entire view, leaving no valid range for forgetting "
                "events. Consider adjusting keep_first or max_size parameters."
            )

        return self._generate_condensation(
            forgotten_events=forgotten_events,
            summary_offset=summary_offset,
        )
