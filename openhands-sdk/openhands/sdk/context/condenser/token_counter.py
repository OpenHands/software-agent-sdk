"""Incremental, exact token counting for the condenser hot path.

``get_total_token_count`` re-formats and re-tokenises the whole view on every
agent step, which is the dominant CPU cost of the loop (~100 ms/step on a real
900-step conversation). LiteLLM's counter is exactly additive over the message
list, so a total can be assembled from per-message counts instead of re-walking
the whole view.

The subtlety is that the event-to-message projection is *not* append-only: when
an ``ActionEvent`` arrives it appends a ``tool_call`` to an earlier assistant
message (and adjacent user messages can coalesce), so a position-keyed prefix
cache silently goes stale. The cache here is therefore keyed by message
*content* and only re-counts entries that actually changed.

Two safeguards keep the cached total honest:

* ``LLM.token_count_is_additive`` rejects, structurally, counting paths that are
  additive over one message but not over the whole list (prompt-mocked tool
  calling, chat-template tokenizers).
* The empirical check that guards the sum is only run on a **multi-message**
  input, because with a single message the sum trivially equals the full count
  and proves nothing. Until it has run, ``count`` returns an exact full recount.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.llm_convertible.system import SystemPromptEvent


if TYPE_CHECKING:
    from openhands.sdk.llm import LLM, Message


class IncrementalTokenCounter:
    """Cache the token count of a growing event view.

    Usage mirrors ``get_total_token_count``::

        counter = IncrementalTokenCounter(llm)
        total = counter.count(events)

    The first call counts every message individually; later calls re-count only
    messages whose content changed. The result is integer-identical to a full
    ``get_total_token_count`` on the same events, and it degrades gracefully to
    a full recount for any message the configured counting path does not treat
    additively (notably a chat-template tokenizer, which renders the whole
    message list into a single prompt string).

    Messages produced by ``events_to_messages`` are freshly built objects and the
    projection never mutates a previously returned message, so comparing the
    stored message against the new one with ``==`` is both exact and cheap.
    """

    def __init__(self, llm: "LLM") -> None:
        self._llm = llm
        # Parallel to the last message list: the message object and its
        # per-message token count.
        self._messages: list[Message] = []
        self._counts: list[int] = []
        # Whether the per-message total can be combined additively. ``None``
        # until verified on the first call; ``False`` disables the cache.
        self._additive: bool | None = None
        # ``count([], tools)`` for the tool configuration currently in use. The
        # tools object is held so its identity stays stable (and so it cannot be
        # freed and re-allocated at the same address).
        self._tools: object | None = None
        self._empty_count: int | None = None
        self._add_security_risk_prediction = False

    def count(self, events: Sequence[LLMConvertibleEvent]) -> int:
        messages = LLMConvertibleEvent.events_to_messages(list(events))
        tools = next(
            (event.tools for event in events if isinstance(event, SystemPromptEvent)),
            None,
        )
        tools = tools or None
        add_security_risk_prediction = bool(tools)

        # A different tool schema (or a different security-risk flag) changes the
        # additivity constant, so the cache must be rebuilt.
        if tools is not self._tools or (
            add_security_risk_prediction != self._add_security_risk_prediction
        ):
            self._invalidate()
            self._tools = tools
            self._add_security_risk_prediction = add_security_risk_prediction

        if self._additive is None and not self._llm.token_count_is_additive(
            has_tools=add_security_risk_prediction
        ):
            # A counting path that is additive over a single message but not over
            # the whole list (prompt-mocked tool calling, chat-template
            # tokenizers) would pass the empirical check below on a one-message
            # view and then silently diverge. Disable the cache up front.
            self._additive = False

        if self._additive is False:
            return self._llm.get_token_count(
                messages,
                tools=tools,
                add_security_risk_prediction=add_security_risk_prediction,
            )

        if len(messages) < len(self._messages):
            # The view shrank (condensation): drop the tail and recount what
            # remains, which the content comparison below takes care of.
            del self._messages[len(messages) :]
            del self._counts[len(messages) :]

        while len(self._messages) < len(messages):
            self._messages.append(None)  # type: ignore[arg-type]
            self._counts.append(0)

        for index, message in enumerate(messages):
            previous = self._messages[index]
            if previous is None or previous != message:
                self._messages[index] = message
                self._counts[index] = self._llm.get_token_count(
                    [message],
                    tools=tools,
                    add_security_risk_prediction=add_security_risk_prediction,
                )

        if self._empty_count is None:
            self._empty_count = self._llm.get_token_count(
                [],
                tools=tools,
                add_security_risk_prediction=add_security_risk_prediction,
            )
        total = sum(self._counts) - (len(messages) - 1) * self._empty_count

        if self._additive is None and len(messages) >= 2:
            # Verify additivity once, but only on a multi-message input: with a
            # single message the "sum" is trivially equal to the full count, so
            # the comparison proves nothing. Until this runs, ``count`` returns
            # the full recount below, which is always exact.
            full = self._llm.get_token_count(
                messages,
                tools=tools,
                add_security_risk_prediction=add_security_risk_prediction,
            )
            self._additive = full == total
            return full

        if self._additive is None:
            # Still unverified (a one-message view): return the exact recount.
            return self._llm.get_token_count(
                messages,
                tools=tools,
                add_security_risk_prediction=add_security_risk_prediction,
            )

        return total

    def _invalidate(self) -> None:
        self._messages = []
        self._counts = []
        self._additive = None
        self._empty_count = None
