"""Judge whether a conversation is resolved: nothing is left for the person.

A conversation is resolved when the agent's last turn leaves the person with
nothing to do: no question to answer, no pull request to review or merge, no
choice to make and nothing to try out. This is a different question from the
critic's "did the task succeed?": a good answer to a question is resolved, and
a finished pull request that waits for review is not.

The model makes judgments and code makes the decision. The judge asks narrow
yes/no questions (:data:`RESOLUTION_QUESTIONS`): was the request handled,
does the reply ask the person something, hand them a review, leave them a
choice, ask them to check something, report a problem.
:func:`verdict_from_judgments` combines the answers. Any
:class:`~openhands.sdk.llm.decision.DecisionModel` can answer: a decision model
such as TypeSafe's Jev, an open model served by vLLM (both through LiteLLM),
or a chat :class:`~openhands.sdk.llm.LLM` through :class:`LLMDecisionModel`.
"""

import json
import re
from collections.abc import Mapping
from typing import Any, Final, Literal

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
    "verdict_from_judgments",
]

Owed = Literal["nothing", "answer", "review", "decide", "check", "unclear"]

AUTO_RESOLVE_PROBABILITY: Final[float] = 0.85
"""Probability that nothing is left for the person, at or above which a turn
counts as resolved."""

_TEXT_LIMIT: Final[int] = 4000
_ACTIONS_LIMIT: Final[int] = 20

_CONTEXT = (
    "`request` is the person's last message to a coding agent and `reply` is "
    "the agent's final message for that turn; `actions` lists what the agent "
    "did. "
)

# One narrow judgment per question, each answered on its own. Code combines
# them into the verdict (:func:`verdict_from_judgments`), so a judgment can be
# checked, tuned or replaced without touching the others.
RESOLUTION_QUESTIONS: Final[dict[str, Question]] = {
    "handled": Noul(
        instructions=_CONTEXT
        + "Does `reply` show that what `request` asked for was done or answered?",
        criteria=NoulCriteria(
            true="The agent did or answered what was asked, in full.",
            false=(
                "Part of the request is missing, the agent stopped early, or "
                "it only explained what it would do."
            ),
        ),
    ),
    "asks_person": Noul(
        instructions=_CONTEXT
        + "Does `reply` ask the person a question or wait for input from them, "
        "even when worded without a question mark?",
    ),
    "hands_off_review": Noul(
        instructions=_CONTEXT
        + "Does `reply` hand the person a change to review, approve or merge, "
        "such as a pull request that is ready for review?",
    ),
    "offers_choice": Noul(
        instructions=_CONTEXT
        + "Does `reply` lay out options and leave the choice to the person?",
    ),
    "asks_to_check": Noul(
        instructions=_CONTEXT
        + "Does `reply` ask the person to try, test or verify something "
        "themselves?",
    ),
    "reports_problem": Noul(
        instructions=_CONTEXT
        + "Does `reply` report a failure, an error or a blocker that is still "
        "unresolved?",
    ),
}

# What the person owes when each judgment holds.
_OWED_BY_JUDGMENT: Final[dict[str, Owed]] = {
    "asks_person": "answer",
    "hands_off_review": "review",
    "offers_choice": "decide",
    "asks_to_check": "check",
    "reports_problem": "check",
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

    def as_state(self) -> dict[str, Any]:
        return {
            "request": _clip(self.request),
            "reply": _clip(self.reply),
            "actions": self.actions[-_ACTIONS_LIMIT:],
        }


class ResolutionVerdict(BaseModel):
    """The judge's answer for one turn."""

    probability: float = Field(
        ge=0.0, le=1.0, description="Probability that nothing is left for the person."
    )
    owed: Owed = Field(description="Most likely thing the person still owes.")
    judgments: dict[str, float] = Field(
        default_factory=dict, description="Each judgment's probability, by name."
    )
    model: str | None = Field(default=None, description="The model that answered.")

    @property
    def resolved(self) -> bool:
        return self.owed == "nothing" and self.probability >= AUTO_RESOLVE_PROBABILITY

    def note(self) -> str:
        """One short line for the board: ``0.93 nothing`` style."""
        return f"{self.probability:.2f} {self.owed}"


def verdict_from_judgments(
    judgments: Mapping[str, float], model: str | None = None
) -> ResolutionVerdict:
    """Combine the judgments into a verdict.

    Nothing is left when the request was handled and no hand-off holds:
    ``P(handled) * prod(1 - P(hand-off))``, treating the judgments as
    independent. The person owes the likeliest hand-off when it is more likely
    than not, else ``unclear`` (say, the request does not look handled).
    """
    handled = _unit(judgments.get("handled"))
    probability = handled
    for name in _OWED_BY_JUDGMENT:
        probability *= 1.0 - _unit(judgments.get(name))
    owed: Owed = "nothing"
    if probability < AUTO_RESOLVE_PROBABILITY:
        name = max(_OWED_BY_JUDGMENT, key=lambda n: _unit(judgments.get(n)))
        likely = _unit(judgments.get(name)) >= 0.5
        owed = _OWED_BY_JUDGMENT[name] if likely else "unclear"
    return ResolutionVerdict(
        probability=probability,
        owed=owed,
        judgments={name: _unit(p) for name, p in judgments.items()},
        model=model,
    )


async def judge_resolution(
    model: DecisionModel, state: ResolutionState
) -> ResolutionVerdict:
    """Ask ``model`` the resolution judgments about a turn, then combine them."""
    result = await model.adecide(state.as_state(), RESOLUTION_QUESTIONS)
    judgments = {name: result.noul(name).probability for name in RESOLUTION_QUESTIONS}
    return verdict_from_judgments(judgments, model=result.model)


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
        raw = data.get(name)
        raw = raw if isinstance(raw, dict) else {}
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
