"""Tests for the resolution judge's questions, verdict and LLM adapter."""

import json
from collections.abc import Mapping
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import PrivateAttr

from openhands.sdk.conversation.resolution import (
    AUTO_RESOLVE_PROBABILITY,
    RESOLUTION_QUESTIONS,
    LLMDecisionModel,
    ResolutionState,
    judge_resolution,
    verdict_from_judgments,
)
from openhands.sdk.llm import LLM
from openhands.sdk.llm.decision import (
    DecisionModel,
    DecisionReply,
    NoulAnswer,
    Question,
)
from openhands.sdk.llm.message import Message, TextContent


HANDLED = {"handled": 0.97}


class _Fixed(DecisionModel):
    """A decision model that returns set judgments and records the state."""

    _answers: dict[str, float] = PrivateAttr(default_factory=dict)
    _seen: list[tuple[Any, dict[str, Question]]] = PrivateAttr(default_factory=list)

    def send(self, state: Any, questions: Mapping[str, Question]) -> DecisionReply:
        self._seen.append((state, dict(questions)))
        answers = self._answers
        return DecisionReply(
            answers={
                name: NoulAnswer(probability=answers.get(name, 0.0))
                for name in questions
            },
            model="fixed-1",
        )

    async def asend(
        self, state: Any, questions: Mapping[str, Question]
    ) -> DecisionReply:
        return self.send(state, questions)


@pytest.mark.asyncio
async def test_judge_asks_every_judgment_on_the_turn():
    model = _Fixed(model="fixed", num_retries=0)
    model._answers = HANDLED
    state = ResolutionState(request="Explain X", reply="X is ...", actions=["a"])

    verdict = await judge_resolution(model, state)

    assert verdict.resolved
    assert verdict.note() == "0.97 nothing"
    assert verdict.model == "fixed-1"
    sent_state, questions = model._seen[0]
    assert set(questions) == set(RESOLUTION_QUESTIONS)
    assert sent_state["request"] == "Explain X"


@pytest.mark.parametrize(
    ("judgments", "owed"),
    [
        ({"handled": 0.95, "hands_off_review": 0.9}, "review"),
        ({"handled": 0.95, "asks_person": 0.8}, "answer"),
        ({"handled": 0.95, "offers_choice": 0.7, "asks_person": 0.6}, "decide"),
        ({"handled": 0.5, "reports_problem": 0.9}, "check"),
        ({"handled": 0.3}, "unclear"),
    ],
)
def test_a_hand_off_or_unhandled_request_keeps_it_open(judgments, owed):
    verdict = verdict_from_judgments(judgments)

    assert verdict.owed == owed
    assert not verdict.resolved


def test_threshold_applies_to_the_combined_probability():
    at = verdict_from_judgments({"handled": AUTO_RESOLVE_PROBABILITY})
    below = verdict_from_judgments({"handled": 0.95, "asks_to_check": 0.2})

    assert at.resolved
    assert below.probability == pytest.approx(0.76)
    assert not below.resolved


def test_long_reply_keeps_its_end():
    state = ResolutionState(reply="x" * 5000 + "END")
    reply = state.as_state()["reply"]
    assert reply.endswith("END")
    assert len(reply) == 4000


def _llm_replying(text: str) -> MagicMock:
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
            "handled": {"probability": 0.95},
            "hands_off_review": {"probability": 0.9},
        }
    )
    llm = _llm_replying(f"Here you go:\n{reply}")
    model = LLMDecisionModel.model_construct(llm=llm, model="llm", num_retries=0)
    model.model_post_init(None)

    verdict = await judge_resolution(model, ResolutionState(reply="Opened PR #3."))

    assert verdict.owed == "review"
    assert not verdict.resolved
    prompt = llm.completion.call_args.kwargs["messages"][1].content[0].text
    assert "Opened PR #3." in prompt
    assert all(name in prompt for name in RESOLUTION_QUESTIONS)


@pytest.mark.asyncio
async def test_llm_adapter_reads_missing_or_bad_answers_as_no():
    reply = json.dumps({"handled": {"probability": "1.7"}, "asks_person": "yes"})
    model = LLMDecisionModel.model_construct(
        llm=_llm_replying(reply), model="llm", num_retries=0
    )
    model.model_post_init(None)

    verdict = await judge_resolution(model, ResolutionState(reply="Done."))

    assert verdict.judgments["handled"] == 1.0
    assert verdict.judgments["asks_person"] == 0.0
    assert verdict.resolved


@pytest.mark.asyncio
async def test_llm_adapter_without_json_fails():
    model = LLMDecisionModel.model_construct(
        llm=_llm_replying("I think it is resolved."), model="llm", num_retries=0
    )
    model.model_post_init(None)

    with pytest.raises(ValueError, match="JSON"):
        await judge_resolution(model, ResolutionState(reply="Done."))
