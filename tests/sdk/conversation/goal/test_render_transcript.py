"""One complete test of the exact transcript the judge sees, over a REAL trace.

``fixtures/events.jsonl`` is a trimmed slice of the persisted events of an actual
conversation (a PR-review automation run: ``terminal`` tool calls + observations
+ state updates), loaded with the SDK's real deserializer
(``Event.model_validate_json``). ``fixtures/expected_transcript.txt`` is the
byte-for-byte golden of exactly what ``judge_goal`` feeds the judge -- so this
also guards the rendering and the persisted event format.
"""

from collections import Counter
from pathlib import Path

import pytest

from openhands.sdk.conversation.goal import judge_goal
from openhands.sdk.conversation.goal.judge import _render_transcript
from openhands.sdk.event import Event, LLMConvertibleEvent, SystemPromptEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.testing import TestLLM


# The trace's `terminal` tool actions need openhands-tools to deserialize their
# kinds; skip in isolated openhands-sdk runs where it is not installed.
pytest.importorskip("openhands.tools.terminal")


_FIXTURES = Path(__file__).parent / "fixtures"


def _load_trace() -> list[Event]:
    lines = (_FIXTURES / "events.jsonl").read_text().splitlines()
    return [Event.model_validate_json(line) for line in lines if line.strip()]


def test_render_transcript_for_judge():
    events = _load_trace()

    # A real trace: agent tool calls + observations + (non-rendered) state updates.
    kinds = Counter(type(e).__name__ for e in events)
    assert kinds["ActionEvent"] and kinds["ObservationEvent"]
    assert kinds["ConversationStateUpdateEvent"]  # present, but NOT LLM-convertible

    convertible = [e for e in events if isinstance(e, LLMConvertibleEvent)]
    transcript = _render_transcript(convertible)

    # Tool calls render as: ActionEvent -> assistant reasoning turn,
    # ObservationEvent -> `tool:` turn.
    assert "assistant: <think>" in transcript
    assert "\n\ntool: " in transcript
    # Secrets are redacted in what the judge sees.
    assert "<secret-hidden>" in transcript

    # The (large) system prompt is excluded by design: prepending a
    # SystemPromptEvent does not change the rendered transcript.
    system_event = SystemPromptEvent(
        system_prompt=TextContent(text="<SOUL>...big system prompt...</SOUL>"),
        tools=[],
    )
    assert _render_transcript([system_event, *convertible]) == transcript

    # The full kernel turns this transcript into a verdict (the judge's feedback).
    judge = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text='{"score": 0.8, "complete": true}')],
            )
        ]
    )
    verdict = judge_goal(judge, "Review PR #3745", events)
    assert verdict.complete is True
    assert verdict.score == 0.8

    # Byte-for-byte: EXACTLY what judge_goal feeds the judge over the real trace.
    expected = (_FIXTURES / "expected_transcript.txt").read_text()
    assert transcript == expected
