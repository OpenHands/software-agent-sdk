"""Tests for the resolution judge's questions, verdict and LLM adapter."""

import json
from collections.abc import Mapping
from typing import Any
from unittest.mock import MagicMock

import pytest

from openhands.sdk.conversation.resolution import (
    AUTO_RESOLVE_PROBABILITY,
    RESOLUTION_QUESTIONS,
    LLMDecisionModel,
    ResolutionState,
    ResolutionVerdict,
    judge_resolution,
)
from openhands.sdk.llm import LLM
from openhands.sdk.llm.decision import (
    ChoiceAnswer,
    DecisionModel,
    DecisionReply,
    NoulAnswer,
    Question,
)
from openhands.sdk.llm.message import Message, TextContent


class _Fixed(DecisionModel):
    """A decision model that returns set answers and records the state."""

    def send(self, state: Any, questions: Mapping[str, Question]) -> DecisionReply:
        self.__dict__.setdefault("seen", []).append((state, dict(questions)))
        return DecisionReply(
            answers={
                "nothing_left": NoulAnswer(probability=0.91),
                "owed": ChoiceAnswer(
                    choice="nothing",
                    confidence=0.8,
                    probabilities={"nothing": 0.8, "review": 0.2},
                ),
            },
            model="fixed-1",
        )

    async def asend(
        self, state: Any, questions: Mapping[str, Question]
    ) -> DecisionReply:
        return self.send(state, questions)


@pytest.mark.asyncio
async def test_judge_asks_both_questions_and_builds_a_verdict():
    model = _Fixed(model="fixed", num_retries=0)
    state = ResolutionState(request="Explain X", reply="X is ...", actions=["a"])

    verdict = await judge_resolution(model, state)

    assert verdict == ResolutionVerdict(
        probability=0.91, owed="nothing", model="fixed-1"
    )
    assert verdict.resolved
    sent_state, questions = model.__dict__["seen"][0]
    assert set(questions) == set(RESOLUTION_QUESTIONS)
    assert sent_state["request"] == "Explain X"


@pytest.mark.parametrize(
    ("probability", "owed", "resolved"),
    [
        (AUTO_RESOLVE_PROBABILITY, "nothing", True),
        (AUTO_RESOLVE_PROBABILITY - 0.01, "nothing", False),
        (0.99, "review", False),
        (0.99, "unclear", False),
    ],
)
def test_verdict_needs_high_probability_and_nothing_owed(probability, owed, resolved):
    verdict = ResolutionVerdict(probability=probability, owed=owed)
    assert verdict.resolved is resolved


def test_long_reply_keeps_its_end():
    state = ResolutionState(reply="x" * 5000 + "END")
    reply = state.as_state()["reply"]
    assert reply.endswith("END")
    assert len(reply) == 4000


def _llm_replying(text: str) -> LLM:
    llm = MagicMock(spec=LLM)
    llm.model = "some-llm"
    llm.completion.return_value = MagicMock(
        message=Message(role="assistant", content=[TextContent(text=text)])
    )
    return llm


@pytest.mark.asyncio
async def test_llm_adapter_answers_the_typed_questions():
    reply = json.dumps(
        {
            "nothing_left": {"probability": 0.2},
            "owed": {"choice": "review", "confidence": 0.7},
        }
    )
    llm = _llm_replying(f"Here you go:\n{reply}")
    model = LLMDecisionModel.model_construct(llm=llm, model="llm", num_retries=0)
    model.model_post_init(None)

    verdict = await judge_resolution(model, ResolutionState(reply="Opened PR #3."))

    assert verdict.probability == 0.2
    assert verdict.owed == "review"
    assert not verdict.resolved
    prompt = llm.completion.call_args.kwargs["messages"][1].content[0].text
    assert "Opened PR #3." in prompt
    assert "nothing_left" in prompt and "owed" in prompt


@pytest.mark.asyncio
async def test_llm_adapter_reads_an_unknown_choice_as_unclear():
    reply = json.dumps(
        {"nothing_left": {"probability": "1.7"}, "owed": {"choice": "dance"}}
    )
    model = LLMDecisionModel.model_construct(
        llm=_llm_replying(reply), model="llm", num_retries=0
    )
    model.model_post_init(None)

    verdict = await judge_resolution(model, ResolutionState(reply="Done."))

    assert verdict.probability == 1.0
    assert verdict.owed == "unclear"
    assert not verdict.resolved
