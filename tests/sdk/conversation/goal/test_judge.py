"""Tests for judge_goal / GoalVerdict (the goal-completion judge kernel)."""

from typing import cast

import pytest
from pydantic import PrivateAttr

from openhands.sdk.conversation.goal import GoalVerdict, judge_goal
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.testing import TestLLM


def _judge(text: str) -> TestLLM:
    """A judge LLM scripted to return a single verdict string."""
    return TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text=text)])]
    )


class _StreamGuardLLM(TestLLM):
    """TestLLM that enforces the real LLM's streaming guard.

    Lets us prove ``judge_goal`` disables streaming before calling
    ``completion()`` (plain ``TestLLM`` ignores ``stream`` entirely).
    """

    def completion(
        self,
        messages,
        tools=None,
        add_security_risk_prediction=False,
        on_token=None,
        call_context=None,
        **kwargs,
    ):
        if self.stream and on_token is None:
            raise ValueError("Streaming requires an on_token callback")
        return super().completion(
            messages,
            tools,
            add_security_risk_prediction,
            on_token,
            call_context,
            **kwargs,
        )


@pytest.mark.parametrize(
    ("response", "score", "complete", "missing"),
    [
        # exact-JSON verdict
        ('{"score": 0.9, "complete": true, "missing": ""}', 0.9, True, ""),
        # incomplete verdict surfaces the missing work
        (
            '{"score": 0.1, "complete": false, "missing": "tests not run"}',
            0.1,
            False,
            "tests not run",
        ),
        # JSON embedded in prose / a code fence is still extracted
        (
            "Here is my verdict:\n```json\n"
            '{"score": 0.4, "complete": false, "missing": "lint"}\n```',
            0.4,
            False,
            "lint",
        ),
        # unparseable -> conservative (keeps the caller working)
        ("I cannot decide.", 0.0, False, "Judge verdict could not be parsed."),
        # out-of-range score is clamped into [0, 1]
        ('{"score": 1.5, "complete": true, "missing": ""}', 1.0, True, ""),
    ],
    ids=["complete", "incomplete", "json-in-fence", "unparseable", "clamped-score"],
)
def test_judge_goal_parses_verdict(response, score, complete, missing):
    verdict = judge_goal(_judge(response), "build it", [])
    assert isinstance(verdict, GoalVerdict)
    assert verdict.score == score
    assert verdict.complete is complete
    assert verdict.missing == missing


def test_judge_goal_disables_streaming_on_judge_llm():
    """A stream=True judge LLM must not trip completion()'s on_token guard."""
    llm = _StreamGuardLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[
                    TextContent(text='{"score": 1.0, "complete": true, "missing": ""}')
                ],
            )
        ],
        usage_id="judge",
        stream=True,
    )
    verdict = judge_goal(llm, "build x", [])  # would raise without the fix
    assert verdict.complete


class _CapturingJudgeLLM(TestLLM):
    """TestLLM that records the messages handed to ``completion()``."""

    _captured_messages: list = PrivateAttr(default_factory=list)

    @property
    def captured_messages(self) -> list:
        return self._captured_messages

    def completion(self, messages, **kwargs):  # type: ignore[override]
        self._captured_messages = list(messages)
        return super().completion(messages, **kwargs)


def test_judge_goal_sends_system_then_user():
    """The judge request must open with a system message before the user payload."""
    llm = cast(
        _CapturingJudgeLLM,
        _CapturingJudgeLLM.from_messages(
            [
                Message(
                    role="assistant",
                    content=[
                        TextContent(
                            text='{"score": 1.0, "complete": true, "missing": ""}'
                        )
                    ],
                )
            ]
        ),
    )

    judge_goal(llm, "build it", [])

    assert [m.role for m in llm.captured_messages] == ["system", "user"]
    # Steering/format instructions live in the system message; data in the user.
    system_text = llm.captured_messages[0].content[0].text
    user_text = llm.captured_messages[1].content[0].text
    assert "STRICT JSON" in system_text
    assert "<objective>" in user_text
    assert "build it" in user_text
