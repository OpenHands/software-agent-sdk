"""Tests for the resolution judge's questions, verdict and LLM adapter."""

import json
from collections.abc import Mapping
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import PrivateAttr, SecretStr

from openhands.sdk.conversation.resolution import (
    AUTO_RESOLVE_PROBABILITY,
    HAND_OFF_CEILING,
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


HANDLED = {"handled": 0.97, "task_done": 1.0}


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
        ({"handled": 0.95, "task_done": 1, "hands_off_review": 0.9}, "review"),
        ({"handled": 0.95, "task_done": 1, "asks_user": 0.8}, "answer"),
        (
            {"handled": 0.95, "task_done": 1, "offers_choice": 0.7, "asks_user": 0.6},
            "decide",
        ),
        ({"handled": 0.5, "task_done": 1, "reports_problem": 0.9}, "check"),
        ({"handled": 0.3, "task_done": 1}, "unfinished"),
        ({"handled": 0.95, "task_done": 0.2}, "unfinished"),
        ({"handled": 0.9, "task_done": 0.9, "asks_to_check": 0.3}, "unclear"),
    ],
)
def test_a_hand_off_or_unhandled_request_keeps_it_open(judgments, owed):
    verdict = verdict_from_judgments(judgments)

    assert verdict.owed == owed
    assert not verdict.resolved


def test_resolves_only_when_done_and_no_hand_off_reaches_the_ceiling():
    at = verdict_from_judgments(
        {"handled": AUTO_RESOLVE_PROBABILITY, "task_done": 1.0, "asks_user": 0.1}
    )
    weak_hand_off = verdict_from_judgments(
        {"handled": 0.95, "task_done": 1.0, "asks_to_check": HAND_OFF_CEILING}
    )
    task_left = verdict_from_judgments({"handled": 1.0, "task_done": 0.84})

    assert at.resolved
    assert at.probability == AUTO_RESOLVE_PROBABILITY
    assert not weak_hand_off.resolved
    assert weak_hand_off.owed == "unclear"
    assert weak_hand_off.probability == pytest.approx(0.8)
    assert task_left.owed == "unfinished"


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
    reply = json.dumps(
        {
            "handled": {"probability": "1.7"},
            "task_done": {"probability": 1},
            "asks_user": "yes",
        }
    )
    model = LLMDecisionModel.model_construct(
        llm=_llm_replying(reply), model="llm", num_retries=0
    )
    model.model_post_init(None)

    verdict = await judge_resolution(model, ResolutionState(reply="Done."))

    assert verdict.judgments["handled"] == 1.0
    assert verdict.judgments["asks_user"] == 0.0
    assert verdict.resolved


@pytest.mark.asyncio
async def test_llm_adapter_without_json_fails():
    model = LLMDecisionModel.model_construct(
        llm=_llm_replying("I think it is resolved."), model="llm", num_retries=0
    )
    model.model_post_init(None)

    with pytest.raises(ValueError, match="JSON"):
        await judge_resolution(model, ResolutionState(reply="Done."))


@pytest.mark.parametrize(
    ("judgments", "needs"),
    [
        ({"asks_user": 0.9}, True),
        ({"offers_choice": 0.6}, True),
        ({"hands_off_review": 0.8}, True),
        ({"asks_to_check": 0.5}, True),
        ({"reports_problem": 0.99}, False),
        ({"asks_user": 0.3}, False),
    ],
)
def test_needs_user_only_when_the_reply_waits_on_them(judgments, needs):
    verdict = verdict_from_judgments({"handled": 0.9, "task_done": 0.9, **judgments})
    assert verdict.needs_user is needs


def test_state_carries_the_task_and_recent_earlier_requests():
    state = ResolutionState(
        task="t" * 1500, earlier=[f"r{i}" for i in range(8)], request="now"
    ).as_state()

    assert state["task"].startswith("…") and len(state["task"]) == 1000
    assert state["earlier"] == ["r3", "r4", "r5", "r6", "r7"]


# resolution_judge: the saved "resolution_judge" profile


def test_the_judge_saves_its_profile_from_the_conversation_llm(tmp_path):
    from openhands.sdk.conversation.resolution import (
        RESOLUTION_JUDGE_PROFILE_NAME,
        resolution_judge,
    )
    from openhands.sdk.llm.llm_profile_store import LLMProfileStore

    store = LLMProfileStore(tmp_path)
    own = LLM(model="openai/own-model", api_key="sk-own", usage_id="agent")

    judge = resolution_judge(own, store)

    assert [n.removesuffix(".json") for n in store.list()] == [
        RESOLUTION_JUDGE_PROFILE_NAME
    ]
    saved = store.load(RESOLUTION_JUDGE_PROFILE_NAME)
    assert saved.model == "openai/own-model"
    assert isinstance(saved.api_key, SecretStr)
    assert saved.api_key.get_secret_value() == "sk-own"
    assert judge.llm.model == "openai/own-model"
    assert judge.llm.usage_id == RESOLUTION_JUDGE_PROFILE_NAME
    assert judge.llm.num_retries == 0


def test_the_judge_uses_the_saved_profile_over_the_conversation_llm(tmp_path):
    from openhands.sdk.conversation.resolution import (
        RESOLUTION_JUDGE_PROFILE_NAME,
        resolution_judge,
    )
    from openhands.sdk.llm.llm_profile_store import LLMProfileStore

    store = LLMProfileStore(tmp_path)
    store.save(
        RESOLUTION_JUDGE_PROFILE_NAME,
        LLM(model="openai/judge-model", api_key="sk-judge", usage_id="judge"),
        include_secrets=True,
    )
    own = LLM(model="openai/own-model", api_key="sk-own", usage_id="agent")

    judge = resolution_judge(own, store)

    assert judge.llm.model == "openai/judge-model"
    assert store.load(RESOLUTION_JUDGE_PROFILE_NAME).model == "openai/judge-model"


def test_the_judge_profile_is_encrypted_under_the_stores_cipher(tmp_path):
    from openhands.sdk.conversation.resolution import (
        RESOLUTION_JUDGE_PROFILE_NAME,
        resolution_judge,
    )
    from openhands.sdk.llm.llm_profile_store import LLMProfileStore
    from openhands.sdk.utils.cipher import Cipher

    store = LLMProfileStore(tmp_path, cipher=Cipher("server-key"))
    own = LLM(model="openai/own-model", api_key="sk-own", usage_id="agent")

    resolution_judge(own, store)
    judge = resolution_judge(own, store)  # this call loads the saved profile

    saved = (tmp_path / f"{RESOLUTION_JUDGE_PROFILE_NAME}.json").read_text()
    assert "sk-own" not in saved
    assert isinstance(judge.llm.api_key, SecretStr)
    assert judge.llm.api_key.get_secret_value() == "sk-own"


def test_the_judges_deprecated_cipher_argument_still_decrypts(tmp_path, monkeypatch):
    """``resolution_judge(cipher=...)`` was published in 1.63.0."""
    from openhands.sdk.conversation.resolution import resolution_judge
    from openhands.sdk.llm.llm_profile_store import LLMProfileStore
    from openhands.sdk.utils import deprecation
    from openhands.sdk.utils.cipher import Cipher

    monkeypatch.setattr(deprecation, "_current_version", lambda: "1.65.0")
    cipher = Cipher("server-key")
    own = LLM(model="openai/own-model", api_key="sk-own", usage_id="agent")
    resolution_judge(own, LLMProfileStore(tmp_path, cipher=cipher))

    with pytest.warns(
        deprecation.DeprecatedWarning, match=r"resolution_judge\(cipher="
    ):
        judge = resolution_judge(own, LLMProfileStore(tmp_path), cipher=cipher)

    assert isinstance(judge.llm.api_key, SecretStr)
    assert judge.llm.api_key.get_secret_value() == "sk-own"
