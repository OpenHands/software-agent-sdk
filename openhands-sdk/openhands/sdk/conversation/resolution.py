"""Judge whether a conversation is resolved: nothing is left for the person.

A conversation is resolved when the agent's last turn leaves the person with
nothing to do: no question to answer, no pull request to review or merge, no
choice to make and nothing to try out. This is a different question from the
critic's "did the task succeed?": a good answer to a question is resolved, and
a finished pull request that waits for review is not.

The judge asks typed questions (:data:`RESOLUTION_QUESTIONS`) of a
:class:`~openhands.sdk.llm.decision.DecisionModel`, so any backend fits: a
decision model such as TypeSafe's Jev, an open model served by vLLM (both
through LiteLLM), or a chat :class:`~openhands.sdk.llm.LLM` through
:class:`LLMDecisionModel`. The questions and the rule that turns answers into
a verdict stay the same whichever model answers.
"""

import json
import re
from collections.abc import Mapping
from typing import Any, Final, Literal, cast, get_args

from pydantic import BaseModel, Field

from openhands.sdk.llm.decision import (
    Answer,
    Choice,
    ChoiceAnswer,
    DecisionModel,
    DecisionReply,
    Noul,
    NoulAnswer,
    NoulCriteria,
    Question,
)
from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.message import Message, TextContent
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

__all__ = [
    "AUTO_RESOLVE_PROBABILITY",
    "LLMDecisionModel",
    "Owed",
    "RESOLUTION_QUESTIONS",
    "ResolutionState",
    "ResolutionVerdict",
    "judge_resolution",
]

Owed = Literal["nothing", "answer", "review", "decide", "check", "unclear"]

AUTO_RESOLVE_PROBABILITY: Final[float] = 0.85
"""Probability of "nothing left" at or above which a turn counts as resolved,
when the most likely thing the person owes is also ``nothing``."""

_TEXT_LIMIT: Final[int] = 4000
_ACTIONS_LIMIT: Final[int] = 20

RESOLUTION_QUESTIONS: Final[dict[str, Question]] = {
    "nothing_left": Noul(
        instructions=(
            "`request` is what the person last asked for in a conversation "
            "with a coding agent, and `reply` is the agent's final message for "
            "that turn. `actions` lists what the agent did, and `open_prs` "
            "the pull requests it opened that are not merged yet. Is the "
            "person's request fully handled, so that nothing is left for the "
            "person to do in this conversation?"
        ),
        criteria=NoulCriteria(
            true=(
                "The request is done or answered, and the reply does not ask "
                "the person anything, wait on them, or hand them work."
            ),
            false=(
                "The person still has something to do: answer a question, "
                "review or merge a pull request, make a choice, try or check "
                "something, or fix a problem the agent could not. Also false "
                "when the agent stopped early, failed, or only did part of "
                "the request."
            ),
        ),
    ),
    "owed": Choice(
        instructions=(
            "What is the main thing the person still has to do in this "
            "conversation after `reply`?"
        ),
        criteria={
            "nothing": "Nothing: the request is handled.",
            "answer": "Answer a question the agent asked, or give missing input.",
            "review": "Review, approve or merge a pull request or other change.",
            "decide": "Choose between options the agent laid out.",
            "check": "Try, test or verify something by hand, or fix a failure.",
            "unclear": "The reply does not make clear whether the work is done.",
        },
    ),
}


class ResolutionState(BaseModel):
    """What the judge sees of one turn."""

    request: str | None = Field(
        default=None, description="The person's message that started the turn."
    )
    reply: str | None = Field(default=None, description="The agent's final message.")
    actions: list[str] = Field(
        default_factory=list, description="Short summaries of the agent's actions."
    )
    open_prs: list[str] = Field(
        default_factory=list,
        description="Pull requests the agent opened that are still open.",
    )

    def as_state(self) -> dict[str, Any]:
        return {
            "request": _clip(self.request),
            "reply": _clip(self.reply),
            "actions": self.actions[-_ACTIONS_LIMIT:],
            "open_prs": self.open_prs,
        }


class ResolutionVerdict(BaseModel):
    """The judge's answer for one turn."""

    probability: float = Field(
        ge=0.0, le=1.0, description="Probability that nothing is left for the person."
    )
    owed: Owed = Field(description="Most likely thing the person still owes.")
    model: str | None = Field(default=None, description="The model that answered.")

    @property
    def resolved(self) -> bool:
        return self.owed == "nothing" and self.probability >= AUTO_RESOLVE_PROBABILITY

    def note(self) -> str:
        """One short line for the board: ``0.93 nothing`` style."""
        return f"{self.probability:.2f} {self.owed}"


async def judge_resolution(
    model: DecisionModel, state: ResolutionState
) -> ResolutionVerdict:
    """Ask ``model`` whether anything is left for the person after a turn."""
    result = await model.adecide(state.as_state(), RESOLUTION_QUESTIONS)
    choice = result.choice("owed").choice
    owed = cast(Owed, choice) if choice in get_args(Owed) else "unclear"
    return ResolutionVerdict(
        probability=_unit(result.noul("nothing_left").probability),
        owed=owed,
        model=result.model,
    )


class LLMDecisionModel(DecisionModel):
    """Answer decision questions with a chat :class:`LLM`.

    For people without a decision model: the person's own LLM answers the same
    typed questions in JSON. Its probabilities are self-reported, so they are
    less calibrated than a decision model's.
    """

    llm: LLM = Field(description="The chat LLM that answers.")
    model: str = Field(default="llm", description="Name used for metrics and spans.")

    def send(self, state: Any, questions: Mapping[str, Question]) -> DecisionReply:
        response = self.llm.completion(
            messages=[
                Message(role="system", content=[TextContent(text=_LLM_SYSTEM)]),
                Message(
                    role="user",
                    content=[TextContent(text=_llm_prompt(state, questions))],
                ),
            ],
        )
        text = "".join(
            c.text for c in response.message.content if isinstance(c, TextContent)
        )
        return DecisionReply(
            answers=_parse_llm_answers(text, questions), model=self.llm.model
        )

    async def asend(
        self, state: Any, questions: Mapping[str, Question]
    ) -> DecisionReply:
        import asyncio

        return await asyncio.to_thread(self.send, state, questions)


_LLM_SYSTEM = (
    "You judge conversations between a person and a coding agent. Answer each "
    "question about the given state. Reply with one JSON object and nothing "
    "else."
)


def _llm_prompt(state: Any, questions: Mapping[str, Question]) -> str:
    lines = ["State:", json.dumps(state, ensure_ascii=False, indent=1), ""]
    lines.append("Questions:")
    shape: dict[str, Any] = {}
    for name, question in questions.items():
        spec = question.model_dump(exclude_none=True)
        lines.append(f"- {name}: {json.dumps(spec, ensure_ascii=False)}")
        if isinstance(question, Noul):
            shape[name] = {"probability": "<0..1, probability the answer is yes>"}
        elif isinstance(question, Choice):
            shape[name] = {
                "choice": "<one of " + ", ".join(question.criteria) + ">",
                "confidence": "<0..1>",
            }
        else:
            raise ValueError(f"LLMDecisionModel cannot answer {question.type!r}")
    lines += ["", "Reply as JSON shaped like:", json.dumps(shape, indent=1)]
    return "\n".join(lines)


_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _parse_llm_answers(
    text: str, questions: Mapping[str, Question]
) -> dict[str, Answer]:
    match = _JSON_OBJECT.search(text)
    if match is None:
        raise ValueError("The LLM judge did not reply with JSON")
    data = json.loads(match.group(0))
    answers: dict[str, Answer] = {}
    for name, question in questions.items():
        raw = data.get(name) or {}
        if isinstance(question, Noul):
            answers[name] = NoulAnswer(probability=_unit(raw.get("probability")))
        elif isinstance(question, Choice):
            options = list(question.criteria)
            choice = raw.get("choice")
            if choice not in options:
                choice = "unclear" if "unclear" in options else options[-1]
            confidence = _unit(raw.get("confidence"))
            rest = (1.0 - confidence) / max(len(options) - 1, 1)
            answers[name] = ChoiceAnswer(
                choice=choice,
                confidence=confidence,
                probabilities={
                    option: confidence if option == choice else rest
                    for option in options
                },
            )
    return answers


def _unit(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(max(number, 0.0), 1.0)


def _clip(text: str | None) -> str | None:
    if text is None or len(text) <= _TEXT_LIMIT:
        return text
    # Keep the end of a long reply: it says how the turn ended.
    return "…" + text[-(_TEXT_LIMIT - 1) :]
