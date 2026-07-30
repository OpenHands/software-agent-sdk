import os
import time
from collections.abc import Sequence
from enum import Enum
from typing import Any, Final

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
from openhands.sdk.event.llm_convertible import MessageEvent, SystemPromptEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.exceptions import LLMContextWindowExceedError
from openhands.sdk.logger import get_logger
from openhands.sdk.observability.laminar import observe
from openhands.sdk.utils import maybe_truncate


logger = get_logger(__name__)

_TEST_MAX_SIZE_ENV: Final[str] = "OH_CONDENSER_TEST_MAX_SIZE"


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
    target_size: int | None = Field(default=None, gt=0)
    """Optional target number of events after condensation. When unset, the
    existing half-size behavior is preserved.
    """
    max_tokens: int | None = None
    auto_compact_ratio: float = Field(default=0.9, gt=0.0, lt=1.0)
    """Fraction of the agent model's input window at which token-based
    condensation starts when ``max_tokens`` is not configured explicitly.
    """

    summary_input_ratio: float = Field(default=0.6, gt=0.0, lt=1.0)
    """Maximum fraction of the summarizer model's input window used by the
    condensation prompt. The remaining window is reserved for output and provider
    overhead.
    """

    max_summary_retries: int = Field(default=5, ge=0)
    """Maximum number of oldest-first trims after the summarizer reports context
    overflow.
    """

    keep_first: int = Field(default=2, ge=0)
    """Minimum number of events to preserve at the start of the view. The first
    `keep_first` events in the conversation will never be condensed or summarized.
    """

    keep_last_user_turns: int = Field(default=0, ge=0)
    """Number of recent user turns to preserve verbatim. A turn starts at a user
    ``MessageEvent`` and includes every following event up to the next user message,
    so skill context, assistant actions, and tool results remain together.
    """

    minimum_progress: float = Field(default=0.1, gt=0.0, lt=1.0)
    """Minimum fraction of events that must be condensed (0.0-1.0). If fewer than
    this proportion of events would be forgotten, condensation is treated as an error.
    Default 0.1 means at least 10% of events must be condensed.
    """
    """Minimum ratio of the view to be condensed. Condensations below this threshold
    are treated as errors.
    """

    hard_context_reset_max_retries: int = Field(default=5, gt=0)
    """Number of attempts to perform hard context reset before raising an error."""

    hard_context_reset_context_scaling: float = Field(default=0.8, gt=0.0, lt=1.0)
    """When performing hard context reset, if the summarization fails, reduce the max
    size of each event string by this factor and retry.
    """

    @model_validator(mode="before")
    @classmethod
    def _apply_test_max_size_override(cls, data: Any) -> Any:
        raw_max_size = os.getenv(_TEST_MAX_SIZE_ENV)
        if raw_max_size is None or not isinstance(data, dict):
            return data

        try:
            max_size = int(raw_max_size)
        except ValueError as e:
            raise ValueError(f"{_TEST_MAX_SIZE_ENV} must be a positive integer") from e
        if max_size <= 0:
            raise ValueError(f"{_TEST_MAX_SIZE_ENV} must be a positive integer")

        logger.warning(
            "Overriding condenser max_size from %s to %d via %s. "
            "This environment variable is intended for local testing only.",
            data.get("max_size", "default"),
            max_size,
            _TEST_MAX_SIZE_ENV,
        )
        overridden_data = {**data, "max_size": max_size}
        target_size = overridden_data.get("target_size")
        if isinstance(target_size, int) and target_size >= max_size:
            overridden_data["target_size"] = max_size // 2
        return overridden_data

    @model_validator(mode="after")
    def validate_keep_first_vs_max_size(self):
        target_size = self.target_size or self.max_size // 2
        if target_size >= self.max_size:
            raise ValueError("target_size must be less than max_size")
        events_from_tail = target_size - self.keep_first - 1
        if events_from_tail <= 0:
            raise ValueError(
                "keep_first must leave room for a summary and retained suffix at "
                "the configured target_size"
            )
        return self

    @model_validator(mode="after")
    def _disable_streaming_for_summary(self):
        # Summaries are consumed whole with no on_token callback, which a
        # streaming LLM requires. Disable streaming once so every summary path
        # is covered. model_copy is non-mutating and shares usage_id/metrics,
        # so summary tokens stay attributed to the conversation.
        if self.llm.stream:
            self.llm = self.llm.model_copy(update={"stream": False})
        return self

    def handles_condensation_requests(self) -> bool:
        return True

    def _effective_max_tokens(self, agent_llm: LLM | None) -> int | None:
        if self.max_tokens is not None:
            return self.max_tokens
        if agent_llm is None:
            return None

        context_window = agent_llm.effective_max_input_tokens
        if not isinstance(context_window, int) or isinstance(context_window, bool):
            return None
        return max(1, int(context_window * self.auto_compact_ratio))

    def _summary_input_limit(self) -> int | None:
        context_window = self.llm.effective_max_input_tokens
        if not isinstance(context_window, int) or isinstance(context_window, bool):
            return None
        return max(1, int(context_window * self.summary_input_ratio))

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

        # Reason 2: An explicit or model-derived token limit is exceeded.
        effective_max_tokens = self._effective_max_tokens(agent_llm)
        if effective_max_tokens is not None and agent_llm is not None:
            total_tokens = get_total_token_count(view.events, agent_llm)
            if total_tokens >= effective_max_tokens:
                logger.info(
                    "Condenser token limit exceeded: total_tokens=%d max_tokens=%d "
                    "events=%d",
                    total_tokens,
                    effective_max_tokens,
                    len(view),
                )
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

        # Token pressure is a hard requirement in benchmark runs that use a fixed
        # local model context: sending the next request can fail before the recovery
        # path has a chance to run. Treat event-count pressure as soft because that
        # threshold is only a history-management heuristic.
        if Reason.TOKENS in reasons:
            return CondensationRequirement.HARD

        # If the remaining reasons are for resource constraints, we can treat them as
        # a soft requirement. We want to condense when we can, but there's still space
        # in the context window or we'd also see Reason.REQUEST.
        resource_reasons = {Reason.EVENTS}
        if reasons.issubset(resource_reasons):
            return CondensationRequirement.SOFT

        # Requests -- whether they come from the user or the agent -- are always hard
        # requirements. We need to condense now because:
        # 1. the user expects it
        # 2. the agent has no more room in the context window and can't continue
        if Reason.REQUEST in reasons:
            return CondensationRequirement.HARD

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

        event_strings = [
            maybe_truncate(str(forgotten_event), truncate_after=max_event_str_length)
            for forgotten_event in forgotten_events
        ]
        event_strings = self._fit_summary_input(event_strings)

        retries = 0
        while True:
            messages = self._summary_messages(event_strings)
            try:
                llm_t0 = time.monotonic()
                # Do not pass extra_body explicitly. The LLM handles forwarding
                # litellm_extra_body only when it is non-empty.
                llm_response = self.llm.completion(messages=messages)
                llm_ms = (time.monotonic() - llm_t0) * 1000
                break
            except LLMContextWindowExceedError as e:
                if retries >= self.max_summary_retries or not self._trim_summary_input(
                    event_strings
                ):
                    raise NoCondensationAvailableException(
                        f"Summarization LLM call failed: {e}"
                    ) from e
                retries += 1
                logger.warning(
                    "Summarization context window exceeded; trimmed oldest summary "
                    "input and retrying (%d/%d).",
                    retries,
                    self.max_summary_retries,
                )
            except Exception as e:
                raise NoCondensationAvailableException(
                    f"Summarization LLM call failed: {e}"
                ) from e

        # Extract summary from the LLMResponse message
        summary = None
        if llm_response.message.content:
            first_content = llm_response.message.content[0]
            if isinstance(first_content, TextContent):
                summary = first_content.text

        condensation = Condensation(
            forgotten_event_ids={event.id for event in forgotten_events},
            summary=summary,
            summary_offset=summary_offset,
            llm_response_id=llm_response.id,
        )
        logger.info(
            "[perf] condenser.summarize event_id=%s llm_response_id=%s "
            "llm_ms=%.1f n_forgotten=%d n_summary_events=%d retries=%d",
            condensation.id,
            llm_response.id,
            llm_ms,
            len(forgotten_events),
            len(event_strings),
            retries,
        )
        return condensation

    def _summary_messages(self, event_strings: Sequence[str]) -> list[Message]:
        prompt = render_template(
            os.path.join(os.path.dirname(__file__), "prompts"),
            "summarizing_prompt.j2",
            events=event_strings,
        )
        return [Message(role="user", content=[TextContent(text=prompt)])]

    def _summary_token_count(self, event_strings: Sequence[str]) -> int | None:
        token_count = self.llm.get_token_count(self._summary_messages(event_strings))
        if not isinstance(token_count, int) or isinstance(token_count, bool):
            return None
        return token_count

    def _fit_summary_input(self, event_strings: list[str]) -> list[str]:
        limit = self._summary_input_limit()
        if limit is None:
            return event_strings

        fitted = event_strings.copy()
        while len(fitted) > 1:
            token_count = self._summary_token_count(fitted)
            if token_count is None or token_count <= limit:
                return fitted
            fitted.pop(0)

        token_count = self._summary_token_count(fitted)
        if token_count is None or token_count <= limit:
            return fitted

        while self._trim_summary_input(fitted):
            token_count = self._summary_token_count(fitted)
            if token_count is None or token_count <= limit:
                break
        return fitted

    @staticmethod
    def _trim_summary_input(event_strings: list[str]) -> bool:
        if len(event_strings) > 1:
            event_strings.pop(0)
            return True
        if not event_strings or len(event_strings[0]) <= 1:
            return False

        current = event_strings[0]
        next_length = max(1, int(len(current) * 0.8))
        event_strings[0] = maybe_truncate(current, truncate_after=next_length)
        return event_strings[0] != current

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
        configured_target_size = None
        if self.target_size is not None:
            configured_target_size = max(
                self.keep_first + 2,
                min(self.target_size, len(view) // 2),
            )

        if Reason.REQUEST in reasons:
            target_size = (
                configured_target_size
                if configured_target_size is not None
                else len(view) // 2
            )
            suffix_events_to_keep.add(target_size - self.keep_first - 1)

        if Reason.EVENTS in reasons:
            target_size = (
                configured_target_size
                if configured_target_size is not None
                else self.max_size // 2
            )
            suffix_events_to_keep.add(target_size - self.keep_first - 1)

        if Reason.TOKENS in reasons:
            # Compute the number of tokens we need to eliminate to be under half the
            # effective limit. The limit may be explicit or derived from the model.
            assert agent_llm is not None
            effective_max_tokens = self._effective_max_tokens(agent_llm)
            assert effective_max_tokens is not None

            total_tokens = get_total_token_count(view.events, agent_llm)
            tokens_to_reduce = total_tokens - (effective_max_tokens // 2)

            suffix_events_to_keep.add(
                get_suffix_length_for_token_reduction(
                    events=view.events[self.keep_first :],
                    llm=agent_llm,
                    token_reduction=tokens_to_reduce,
                    base_events=view.events[: self.keep_first],
                )
            )
            if configured_target_size is not None:
                suffix_events_to_keep.add(configured_target_size - self.keep_first - 1)

        # We might have multiple reasons to condense, so pick the strictest condensation
        # to ensure all resource constraints are met.
        events_from_tail = min(suffix_events_to_keep)

        protected_prefix_end, protected_suffix_start = self._protected_bounds(view)

        # Calculate naive forgetting end (without considering atomic boundaries),
        # but never cross into the recent user turns preserved verbatim.
        naive_end = min(len(view) - events_from_tail, protected_suffix_start)

        manipulation_indices = view.manipulation_indices

        # Find actual forgetting_start after the configured prefix and every system
        # prompt. System prompts are detected by type rather than relying only on their
        # usual position near the beginning of the event stream.
        forgetting_start = manipulation_indices.find_next(protected_prefix_end)

        valid_ends = [
            index
            for index in manipulation_indices
            if naive_end <= index <= protected_suffix_start
        ]
        if valid_ends:
            forgetting_end = min(valid_ends)
        else:
            safe_ends = [
                index
                for index in manipulation_indices
                if index <= protected_suffix_start
            ]
            forgetting_end = max(safe_ends, default=forgetting_start)

        # Extract events to forget using boundary-aware indices
        forgotten_events = view[forgetting_start:forgetting_end]

        # Summary offset is the same as forgetting_start
        return forgotten_events, forgetting_start

    def _protected_bounds(self, view: View) -> tuple[int, int]:
        """Return the exclusive prefix end and inclusive recent-turn boundary."""
        system_prompt_indices = [
            index
            for index, event in enumerate(view.events)
            if isinstance(event, SystemPromptEvent)
        ]
        protected_prefix_end = min(
            len(view),
            max(
                self.keep_first,
                max(system_prompt_indices, default=-1) + 1,
            ),
        )

        protected_suffix_start = len(view)
        if self.keep_last_user_turns:
            user_message_indices = [
                index
                for index, event in enumerate(view.events)
                if isinstance(event, MessageEvent) and event.source == "user"
            ]
            if user_message_indices:
                protected_index = max(
                    0, len(user_message_indices) - self.keep_last_user_turns
                )
                protected_suffix_start = user_message_indices[protected_index]

        return protected_prefix_end, protected_suffix_start

    def _protected_middle_events(
        self, view: View
    ) -> tuple[Sequence[LLMConvertibleEvent], int]:
        protected_prefix_end, protected_suffix_start = self._protected_bounds(view)
        manipulation_indices = view.manipulation_indices
        summary_offset = manipulation_indices.find_next(protected_prefix_end)
        valid_ends = [
            index
            for index in manipulation_indices
            if summary_offset <= index <= protected_suffix_start
        ]
        forgetting_end = max(valid_ends, default=summary_offset)
        return view[summary_offset:forgetting_end], summary_offset

    @observe(ignore_inputs=["view", "agent_llm"])
    def hard_context_reset(
        self,
        view: View,
        agent_llm: LLM | None = None,  # noqa: ARG002
    ) -> Condensation | None:
        """Perform a hard context reset while preserving protected context.

        Depending on how the hard context reset is triggered, this may fail (e.g., if
        the view is too large for the summarizing LLM to handle). In that case, we keep
        trimming down the contents until a summary can be generated.
        """
        max_event_str_length: int | None = None
        attempts_remaining: int = self.hard_context_reset_max_retries
        forgotten_events, summary_offset = self._protected_middle_events(view)
        if not forgotten_events:
            return None

        while attempts_remaining > 0:
            try:
                return self._generate_condensation(
                    forgotten_events=forgotten_events,
                    summary_offset=summary_offset,
                    max_event_str_length=max_event_str_length,
                )
            except Exception as e:
                # If we haven't set a max_event_str_length yet, set it as the largest
                # event string length.
                if max_event_str_length is None:
                    max_event_str_length = max(
                        len(str(event)) for event in forgotten_events
                    )

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
        # summary. If we fail to find an appropriate set of events to forget raise an
        # exception so the conversation can keep going until conditions change.
        try:
            forgotten_events, summary_offset = self._get_forgotten_events(
                view, agent_llm=agent_llm
            )
        except ValueError as e:
            raise NoCondensationAvailableException(
                "Unable to compute forgotten events"
            ) from e

        if not forgotten_events:
            raise NoCondensationAvailableException(
                "Cannot condense 0 events. This typically occurs when a tool loop "
                "spans almost the entire view, leaving no valid range for forgetting "
                "events. Consider adjusting keep_first or max_size parameters."
            )

        if len(forgotten_events) < len(view) * self.minimum_progress:
            raise NoCondensationAvailableException(
                "Cannot apply condensation: events forgotten below minimum progress "
                "threshold."
            )

        return self._generate_condensation(
            forgotten_events=forgotten_events,
            summary_offset=summary_offset,
        )

    # ------------------------------------------------------------------
    # Async variants
    # ------------------------------------------------------------------

    async def _agenerate_condensation(
        self,
        forgotten_events: Sequence[LLMConvertibleEvent],
        summary_offset: int,
        max_event_str_length: int | None = None,
    ) -> Condensation:
        """Async variant of :meth:`_generate_condensation`."""
        assert len(forgotten_events) > 0, "No events to condense."

        event_strings = [
            maybe_truncate(str(fe), truncate_after=max_event_str_length)
            for fe in forgotten_events
        ]
        event_strings = self._fit_summary_input(event_strings)

        retries = 0
        while True:
            messages = self._summary_messages(event_strings)
            try:
                llm_t0 = time.monotonic()
                llm_response = await self.llm.acompletion(messages=messages)
                llm_ms = (time.monotonic() - llm_t0) * 1000
                break
            except LLMContextWindowExceedError as e:
                if retries >= self.max_summary_retries or not self._trim_summary_input(
                    event_strings
                ):
                    raise NoCondensationAvailableException(
                        f"Summarization LLM call failed: {e}"
                    ) from e
                retries += 1
                logger.warning(
                    "Async summarization context window exceeded; trimmed oldest "
                    "summary input and retrying (%d/%d).",
                    retries,
                    self.max_summary_retries,
                )
            except Exception as e:
                raise NoCondensationAvailableException(
                    f"Summarization LLM call failed: {e}"
                ) from e

        summary = None
        if llm_response.message.content:
            first_content = llm_response.message.content[0]
            if isinstance(first_content, TextContent):
                summary = first_content.text

        condensation = Condensation(
            forgotten_event_ids={event.id for event in forgotten_events},
            summary=summary,
            summary_offset=summary_offset,
            llm_response_id=llm_response.id,
        )
        logger.info(
            "[perf] condenser.asummarize event_id=%s llm_response_id=%s "
            "llm_ms=%.1f n_forgotten=%d n_summary_events=%d retries=%d",
            condensation.id,
            llm_response.id,
            llm_ms,
            len(forgotten_events),
            len(event_strings),
            retries,
        )
        return condensation

    async def aget_condensation(
        self, view: View, agent_llm: LLM | None = None
    ) -> Condensation:
        """Async variant of :meth:`get_condensation`."""
        try:
            forgotten_events, summary_offset = self._get_forgotten_events(
                view, agent_llm=agent_llm
            )
        except ValueError as e:
            raise NoCondensationAvailableException(
                "Unable to compute forgotten events"
            ) from e

        if not forgotten_events:
            raise NoCondensationAvailableException(
                "Cannot condense 0 events. This typically occurs when a tool loop "
                "spans almost the entire view, leaving no valid range for "
                "forgetting events. Consider adjusting keep_first or max_size "
                "parameters."
            )

        if len(forgotten_events) < len(view) * self.minimum_progress:
            raise NoCondensationAvailableException(
                "Cannot apply condensation: events forgotten below minimum "
                "progress threshold."
            )

        return await self._agenerate_condensation(
            forgotten_events=forgotten_events,
            summary_offset=summary_offset,
        )

    async def ahard_context_reset(
        self,
        view: View,
        agent_llm: LLM | None = None,  # noqa: ARG002
    ) -> Condensation | None:
        """Async variant of :meth:`hard_context_reset`."""
        max_event_str_length: int | None = None
        attempts_remaining: int = self.hard_context_reset_max_retries
        forgotten_events, summary_offset = self._protected_middle_events(view)
        if not forgotten_events:
            return None

        while attempts_remaining > 0:
            try:
                return await self._agenerate_condensation(
                    forgotten_events=forgotten_events,
                    summary_offset=summary_offset,
                    max_event_str_length=max_event_str_length,
                )
            except Exception as e:
                if max_event_str_length is None:
                    max_event_str_length = max(len(str(ev)) for ev in forgotten_events)
                assert max_event_str_length is not None
                max_event_str_length = int(
                    max_event_str_length * self.hard_context_reset_context_scaling
                )
                logger.warning(
                    f"Hard context reset summarization failed: {e}. "
                    f"Reducing max event size to {max_event_str_length}."
                )
            attempts_remaining -= 1

        logger.error("Hard context reset summarization failed after multiple attempts.")
        return None


# Sizing for the standard summarizing condenser. Kept here so the default agent and
# spawned sub-agents stay in sync.
_DEFAULT_MAX_SIZE: Final[int] = 80
_DEFAULT_KEEP_FIRST: Final[int] = 4


def default_condenser(llm: LLM) -> LLMSummarizingCondenser:
    """Standard summarizing condenser used by the default agent and sub-agents."""
    return LLMSummarizingCondenser(
        llm=llm, max_size=_DEFAULT_MAX_SIZE, keep_first=_DEFAULT_KEEP_FIRST
    )
