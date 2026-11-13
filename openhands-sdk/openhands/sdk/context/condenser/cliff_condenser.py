from collections.abc import Sequence

from pydantic import Field

from openhands.sdk.context.condenser.base import (
    CondensationRequirement,
    NoCondensationAvailableException,
    RollingCondenser,
)
from openhands.sdk.context.condenser.utils import get_total_token_count
from openhands.sdk.context.view import View
from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
    ObservationEvent,
    SystemPromptEvent,
    UserRejectObservation,
)
from openhands.sdk.llm import (
    LLM,
    Message,
    ReasoningItemModel,
    TextContent,
    ThinkingBlock,
)
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


SUMMARY_HEADER = (
    "The following is a summary of your previous actions (long observations omitted):"
)
"""Opening line of every summary."""

PART_SEPARATOR = "\n\n---\n\n"
"""Separator between per-event digests inside a summary."""


def _truncate(text: str, max_chars: int) -> str:
    """Truncate with an ellipsis. ``max_chars <= 0`` means unlimited."""
    text = text or ""
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars] + "..."
    return text


def _text_of(contents: Sequence[object]) -> str:
    """Join the text blocks of a content list, dropping images."""
    return "\n".join(
        c.text for c in contents if isinstance(c, TextContent) and c.text.strip()
    )


def _responses_reasoning_text(item: ReasoningItemModel | None) -> str:
    """Visible plaintext of a Responses reasoning item, never its ciphertext.

    ``encrypted_content`` is deliberately untouched: it is opaque, and a
    summary is plain text that the model reads back, not a block it can
    decrypt.
    """
    if item is None:
        return ""
    lines = [s for s in item.summary if s.strip()]
    lines += [c for c in (item.content or []) if c.strip()]
    return "\n".join(lines)


def _reasoning_text(source: ActionEvent | Message | None, keep: bool) -> str:
    """Reasoning text carried by an event or message, from one channel only.

    An event can hold the same reasoning in more than one field. On Anthropic
    routes litellm derives ``reasoning_content`` from ``thinking_blocks`` by
    concatenating their text, so reading both channels would repeat it -- and
    with several blocks the derived string matches none of them individually,
    so comparing the two is not enough to notice. Blocks therefore win when
    present, and ``reasoning_content`` is the fallback for routes that send no
    blocks (OpenAI-compatible endpoints, gateways that flatten reasoning).

    Redacted blocks carry no readable text and contribute nothing.
    """
    if source is None or not keep:
        return ""
    blocks = [
        b.thinking
        for b in source.thinking_blocks
        if isinstance(b, ThinkingBlock) and b.thinking and b.thinking.strip()
    ]
    if blocks:
        return "\n".join(blocks)
    responses = _responses_reasoning_text(source.responses_reasoning_item)
    if responses:
        return responses
    reasoning = source.reasoning_content
    return reasoning if reasoning and reasoning.strip() else ""


class CliffCondenser(RollingCondenser):
    """Condense history with a fixed-rule digest instead of an LLM call.

    Having no model call to make, this condenser cannot fail to produce a
    condensation, costs nothing, adds no latency, and returns the same summary
    for the same history on every replay of the event log.
    """

    max_tokens: int | None = Field(default=200_000, gt=0)
    """Token budget for the view. Crossing it asks for condensation before the
    model's own limit is in reach. Set to ``None`` to condense only under real
    context pressure from ``agent_llm``, which leaves a model whose limit is
    unknown to ``litellm`` with no trigger at all."""

    keep_first: int = Field(default=2, ge=0)
    """Number of leading events never condensed -- normally the system prompt
    and the opening human message. Floored at the index just past a leading
    ``SystemPromptEvent`` so that one is always protected."""

    keep_recent: int = Field(default=3, ge=1)
    """Number of recent turns kept verbatim. A turn is one LLM response --
    every action in a parallel batch, plus the observations they produced.

    At least one: a view with no verbatim tail is what ``hard_context_reset``
    produces in an emergency, not something to run in steady state."""

    thought_max_chars: int = Field(default=0, ge=0)
    """Cap on assistant text per digested event. 0 keeps it whole."""

    thinking_max_chars: int = Field(default=0, ge=0)
    """Cap on reasoning text per digested event. 0 keeps it whole."""

    cmd_max_chars: int = Field(default=150, ge=0)
    """Cap on the serialized arguments in a tool-call signature."""

    result_max_chars: int = Field(default=500, ge=0)
    """Tool results longer than this are dropped from the summary rather than
    truncated: a prefix of a long result is usually misleading, and the bulk of
    what a history holds is exactly these."""

    human_max_chars: int = Field(default=20_000, ge=0)
    """Cap on human text, which is otherwise kept verbatim. Guards against a
    single huge paste making a summary larger than the region it replaces."""

    keep_thinking: bool = True
    """Keep reasoning as text in summaries. Signed thinking blocks are never
    re-sent from the digested region either way -- only their text."""

    def handles_condensation_requests(self) -> bool:
        """Serve ``/condense`` directly: there is no call to spend on it."""
        return True

    # --- triggering -----------------------------------------------------

    def condensation_requirement(
        self, view: View, agent_llm: LLM | None = None
    ) -> CondensationRequirement | None:
        if view.unhandled_condensation_request:
            return CondensationRequirement.HARD

        if agent_llm is None:
            return None

        limit = agent_llm.effective_max_input_tokens
        if limit is None and self.max_tokens is None:
            return None

        total = get_total_token_count(view.events, agent_llm)

        # The level chosen here only matters if no cut can be made this step:
        # SOFT then sends the view uncondensed, HARD falls through to a
        # context reset. Exceeding the model's context window is fatal, so it
        # has to be HARD. Exceeding a configured budget only costs money, and
        # must never be able to trigger a reset.
        if limit is not None and total > limit:
            logger.info(
                "cliff: context limit exceeded, tokens=%d limit=%d events=%d",
                total,
                limit,
                len(view),
            )
            return CondensationRequirement.HARD

        if self.max_tokens is not None and total > self.max_tokens:
            logger.info(
                "cliff: budget exceeded, tokens=%d budget=%d events=%d",
                total,
                self.max_tokens,
                len(view),
            )
            return CondensationRequirement.SOFT

        return None

    # --- digesting one event --------------------------------------------

    def _digest(self, event: LLMConvertibleEvent) -> list[str]:
        """Digest one event into zero or more summary parts.

        Dispatch is by event class rather than by ``source``: an
        ``AgentErrorEvent`` is sourced from the agent but reaches the model as
        a tool result, and belongs in the summary as one.
        """
        if isinstance(event, ActionEvent):
            return self._digest_action(event)
        if isinstance(event, ObservationEvent):
            body = _text_of(
                [*event.observation.to_llm_content, *event.extended_content]
            )
            return self._digest_result(body)
        if isinstance(event, UserRejectObservation):
            return self._digest_result(f"Action rejected: {event.rejection_reason}")
        if isinstance(event, AgentErrorEvent):
            return self._digest_result(event.error)
        if isinstance(event, MessageEvent):
            return self._digest_message(event)
        # SystemPromptEvent never reaches here (it is inside the protected
        # prefix). A CondensationSummaryEvent does reach it on re-condensation
        # and yields nothing: an older summary is dropped, not merged.
        return []

    def _digest_action(self, event: ActionEvent) -> list[str]:
        lines: list[str] = []
        thinking = _truncate(
            _reasoning_text(event, self.keep_thinking), self.thinking_max_chars
        )
        if thinking:
            lines.append(f"thinking: {thinking}")
        thought = _truncate(_text_of(event.thought), self.thought_max_chars)
        if thought:
            lines.append(f"assistant: {thought}")
        args = _truncate(event.tool_call.arguments, self.cmd_max_chars)
        lines.append(f"[{event.tool_name}] {args}")
        return ["\n".join(lines)]

    def _digest_result(self, body: str) -> list[str]:
        body = body.strip()
        if body and len(body) <= self.result_max_chars:
            return [f"result: {body}"]
        return []

    def _digest_message(self, event: MessageEvent) -> list[str]:
        body = _text_of([*event.llm_message.content, *event.extended_content])
        if event.source == "user":
            text = body.strip()
            if not text:
                return []
            return [f"user: {_truncate(text, self.human_max_chars)}"]

        lines: list[str] = []
        thinking = _truncate(
            _reasoning_text(event.llm_message, self.keep_thinking),
            self.thinking_max_chars,
        )
        if thinking:
            lines.append(f"thinking: {thinking}")
        thought = _truncate(body.strip(), self.thought_max_chars)
        if thought:
            lines.append(f"assistant: {thought}")
        return ["\n".join(lines)] if lines else []

    # --- choosing the cut -----------------------------------------------

    def _protected_prefix(self, events: Sequence[LLMConvertibleEvent]) -> int:
        """Number of leading events that condensation must never touch.

        A fixed count rather than a content boundary, matching
        ``LLMSummarizingCondenser``. Because it does not depend on where the
        first agent event happens to sit, the summary offset is the same index
        on every condensation, so a summary from an earlier one always falls
        inside the region being condensed and is replaced rather than stranded
        in the head.
        """
        protected = self.keep_first
        for i, event in enumerate(events):
            if isinstance(event, SystemPromptEvent):
                return max(protected, i + 1)
        return protected

    def _turn_starts(
        self, events: Sequence[LLMConvertibleEvent], start: int
    ) -> list[int]:
        """Indices in ``events`` at or after ``start`` that begin a turn.

        A turn is one LLM response and everything it produced. Parallel tool
        calls arrive as several ``ActionEvent``s sharing an ``llm_response_id``,
        so the batch opens a single turn.
        """
        starts: list[int] = []
        current_response: str | None = None
        for i in range(start, len(events)):
            event = events[i]
            if isinstance(event, ActionEvent):
                if event.llm_response_id != current_response:
                    starts.append(i)
                    current_response = event.llm_response_id
            elif isinstance(event, MessageEvent) and event.source == "agent":
                starts.append(i)
                current_response = None
        return starts

    def _condense_to(
        self,
        events: Sequence[LLMConvertibleEvent],
        start: int,
        end: int,
    ) -> Condensation:
        """Digest ``events[start:end]`` into a summary that replaces them.

        A summary from an earlier condensation sits at ``start`` and so is
        inside this range: it is forgotten like anything else and digests to
        nothing, which is how an older summary is dropped rather than merged.
        """
        parts: list[str] = []
        for event in events[start:end]:
            parts.extend(self._digest(event))

        joined = PART_SEPARATOR.join(p for p in parts if p)
        summary = f"{SUMMARY_HEADER}\n\n{joined}" if joined else SUMMARY_HEADER

        forgotten = {event.id for event in events[start:end]}

        logger.info(
            "cliff: condensing events[%d:%d], %d forgotten, summary %d chars",
            start,
            end,
            len(forgotten),
            len(summary),
        )

        return Condensation(
            forgotten_event_ids=forgotten,
            summary=summary,
            summary_offset=start,
            # No LLM response produced this summary.
            llm_response_id="",
        )

    def hard_context_reset(
        self,
        view: View,
        agent_llm: LLM | None = None,  # noqa: ARG002
    ) -> Condensation | None:
        """Condense as far as the view legally allows, ignoring ``keep_recent``.

        Reached only when a HARD requirement could not be met because no legal
        cut sat where ``keep_recent`` wanted one. Giving up the recent turns is
        the only lever here, and it costs less than it would for a summarizing
        condenser: those turns still reach the model through their digests.
        """
        events = view.events
        indices = view.manipulation_indices
        try:
            start = indices.find_next(self._protected_prefix(events))
        except ValueError:
            return None
        legal = [i for i in indices if i > start]
        if not legal:
            return None
        return self._condense_to(events, start, max(legal))

    def get_condensation(
        self,
        view: View,
        agent_llm: LLM | None = None,  # noqa: ARG002
    ) -> Condensation:
        events = view.events
        indices = view.manipulation_indices

        try:
            start = indices.find_next(self._protected_prefix(events))
        except ValueError as e:
            raise NoCondensationAvailableException("no legal start index") from e

        turn_starts = self._turn_starts(events, start)
        if len(turn_starts) <= self.keep_recent:
            raise NoCondensationAvailableException(
                f"only {len(turn_starts)} turns, keep_recent={self.keep_recent}"
            )
        end = turn_starts[-self.keep_recent]

        # ``end`` marks where the verbatim tail begins, so snapping it forward
        # to the nearest legal split point widens the compacted region and
        # leaves fewer recent turns verbatim than ``keep_recent`` asked for.
        try:
            end = indices.find_next(end)
        except ValueError as e:
            raise NoCondensationAvailableException(
                f"no legal cut at or after {end}"
            ) from e
        if end <= start:
            raise NoCondensationAvailableException("nothing to condense")

        return self._condense_to(events, start, end)


__all__ = ["CliffCondenser", "SUMMARY_HEADER"]
