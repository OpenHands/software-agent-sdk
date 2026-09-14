"""Test that streamed progress stays consistent with the durable events it becomes.

Chunk ids, reasoning fields and chunk boundaries all come from the provider, so
these invariants can only be checked against real models.
"""

from collections import defaultdict

from openhands.sdk import get_logger
from openhands.sdk.agent.stream_context import (
    StreamAborted,
    StreamDelta,
    StreamProgress,
    StreamProgressCallbackType,
    StreamStarted,
)
from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.sdk.llm import TextContent
from tests.integration.base import BaseIntegrationTest, TestResult


INSTRUCTION = (
    "Use the terminal to run `echo streaming-check`. After it finishes, reply "
    "in two or three sentences describing what the command printed."
)


logger = get_logger(__name__)


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _durable_text(event: MessageEvent | ActionEvent) -> str:
    if isinstance(event, MessageEvent):
        content = event.llm_message.content
    else:
        content = event.thought
    return "".join(c.text for c in content if isinstance(c, TextContent))


def _durable_reasoning(event: MessageEvent | ActionEvent) -> str:
    source = event.llm_message if isinstance(event, MessageEvent) else event
    if source.reasoning_content:
        return source.reasoning_content
    if source.responses_reasoning_item and source.responses_reasoning_item.summary:
        return "".join(source.responses_reasoning_item.summary)
    return "".join(getattr(b, "thinking", "") for b in source.thinking_blocks)


class StreamingProtocolTest(BaseIntegrationTest):
    """Test that streamed progress stays consistent with its durable events."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        self.progress: list[StreamProgress] = []
        kwargs["llm_config"] = {**kwargs["llm_config"], "stream": True}
        super().__init__(*args, **kwargs)

    @property
    def stream_callbacks(self) -> list[StreamProgressCallbackType]:
        return [self.progress.append]

    def verify_result(self) -> TestResult:
        failures = check_stream_progress(
            self.progress, list(self.conversation.state.events)
        )
        if failures:
            return TestResult(success=False, reason="; ".join(failures))
        items = {f.item_id for f in self.progress if isinstance(f, StreamStarted)}
        return TestResult(
            success=True,
            reason=f"{len(items)} streamed item(s) matched their durable events",
        )


def check_stream_progress(progress: list[StreamProgress], events: list) -> list[str]:
    """Return every protocol invariant the captured progress violates."""
    if not any(isinstance(f, StreamStarted) for f in progress):
        return ["no stream progress was emitted with stream=True"]

    failures: list[str] = []
    durable = {
        e.id: (i, e)
        for i, e in enumerate(events)
        if isinstance(e, (MessageEvent, ActionEvent))
    }

    started: dict[str, list[StreamStarted]] = defaultdict(list)
    deltas: dict[tuple[str, int], list[StreamDelta]] = defaultdict(list)
    aborted: dict[str, list[StreamAborted]] = defaultdict(list)
    for frame in progress:
        if isinstance(frame, StreamStarted):
            started[frame.item_id].append(frame)
        elif isinstance(frame, StreamDelta):
            if not any(s.attempt == frame.attempt for s in started[frame.item_id]):
                failures.append(
                    f"delta for {frame.item_id} attempt {frame.attempt} "
                    "arrived before its ItemStarted"
                )
            deltas[(frame.item_id, frame.attempt)].append(frame)
        else:
            aborted[frame.item_id].append(frame)

    for item_id, starts in started.items():
        attempts = [s.attempt for s in starts]
        # A spurious attempt bump makes clients discard everything streamed so
        # far; with no retry injected, every item must stream exactly once.
        if attempts != [1]:
            failures.append(
                f"{item_id} streamed as attempts {attempts} "
                f"({len(deltas[(item_id, attempts[-1])])} delta(s) in the last); "
                "chunk ids changed within one response"
            )

        for attempt in attempts:
            orders = [d.order for d in deltas[(item_id, attempt)]]
            if orders != list(range(len(orders))):
                failures.append(
                    f"{item_id} attempt {attempt} delta order is not "
                    f"contiguous: {orders[:20]}"
                )

        committed = item_id in durable
        if committed == bool(aborted[item_id]) or len(aborted[item_id]) > 1:
            failures.append(
                f"{item_id} must be retired exactly once, got durable={committed} "
                f"aborted={len(aborted[item_id])}"
            )
        if not committed:
            continue

        index, event = durable[item_id]
        anchor = starts[0].anchor_seq
        if anchor is not None and anchor >= index:
            failures.append(
                f"{item_id} anchor_seq {anchor} is not before its event at {index}"
            )

        last = deltas[(item_id, attempts[-1])]
        text = "".join(d.content for d in last if d.kind == "text")
        reasoning = "".join(d.content for d in last if d.kind == "reasoning")
        expected_text = _durable_text(event)
        expected_reasoning = _durable_reasoning(event)
        if text and _normalize(text) != _normalize(expected_text):
            failures.append(
                f"{item_id} text deltas ({len(text)} chars) differ from the "
                f"{type(event).__name__} ({len(expected_text)} chars): "
                f"{text[:80]!r} vs {expected_text[:80]!r}"
            )
        if expected_reasoning.strip() and _normalize(expected_reasoning) in _normalize(
            text
        ):
            failures.append(f"{item_id} reasoning leaked into text deltas")
        if reasoning and _normalize(reasoning) != _normalize(expected_reasoning):
            failures.append(
                f"{item_id} reasoning deltas ({len(reasoning)} chars) differ "
                f"from the durable reasoning ({len(expected_reasoning)} chars)"
            )

    for item_id in aborted:
        if item_id not in started:
            failures.append(f"{item_id} was aborted without being started")

    final = next(
        (
            e
            for e in reversed(events)
            if isinstance(e, MessageEvent) and e.source == "agent"
        ),
        None,
    )
    if final is not None and final.id not in started:
        failures.append(
            f"final agent message {final.id} was not streamed under its own id"
        )

    return failures
