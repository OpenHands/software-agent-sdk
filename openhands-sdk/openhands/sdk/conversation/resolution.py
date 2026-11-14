"""Judge whether a conversation is resolved: nothing is left for the user.

A conversation is resolved when the agent's last turn leaves the user with
nothing to do: no question to answer, no pull request to review or merge, no
choice to make and nothing to try out. This is a different question from the
critic's "did the task succeed?": a good answer to a question is resolved, and
a finished pull request that waits for review is not.

The model makes judgments and code makes the decision. The judge asks narrow
yes/no questions (:data:`RESOLUTION_QUESTIONS`): was the request handled, is
the conversation's whole task done, does the reply ask the user something,
hand them a review, leave them a choice, ask them to check something, report
a problem.
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
    "HAND_OFF_CEILING",
    "EARLIER_REQUESTS_KEPT",
    "LLMDecisionModel",
    "Owed",
    "RESOLUTION_QUESTIONS",
    "ResolutionState",
    "ResolutionVerdict",
    "judge_resolution",
    "verdict_from_judgments",
]

Owed = Literal[
    "nothing", "answer", "review", "decide", "check", "unfinished", "unclear"
]

AUTO_RESOLVE_PROBABILITY: Final[float] = 0.85
"""How sure the judge must be that the work is done (``handled`` and
``task_done``) for a turn to count as resolved."""

HAND_OFF_CEILING: Final[float] = 0.2
"""Every hand-off judgment must stay below this for a turn to resolve."""

LIKELY: Final[float] = 0.5
"""A judgment at or above this probability counts as holding."""

_TEXT_LIMIT: Final[int] = 4000
_CONTEXT_TEXT_LIMIT: Final[int] = 1000
_ACTIONS_LIMIT: Final[int] = 20
EARLIER_REQUESTS_KEPT: Final[int] = 5

_CONTEXT = (
    "`request` is the user's last message to a coding agent and `reply` is "
    "the agent's final message for that turn; `actions` lists what the agent "
    "did. `task` is the message the conversation started with and `earlier` "
    "the user's requests in between, oldest first. Something the user "
    "already took on themselves, or said is not needed, in `request` or "
    "`earlier`, is not left for them. "
)

# One narrow judgment per question, each answered on its own. Code combines
# them into the verdict (:func:`verdict_from_judgments`), so a judgment can be
# checked, tuned or replaced without touching the others.
RESOLUTION_QUESTIONS: Final[dict[str, Question]] = {
    "handled": Noul(
        instructions=_CONTEXT
        + "Do `reply` and `actions` show that what `request` asked for was done "
        "or answered? A `request` that asks for nothing, such as news "
        '("merged", "deployed") or thanks, is handled by acknowledging it, and '
        "an emoji reaction in `actions` acknowledges it even with no `reply`.",
        criteria=NoulCriteria(
            true=(
                "The agent did or answered what was asked, in full, or the "
                "request asked for nothing and the agent acknowledged it."
            ),
            false=(
                "Part of the request is missing, the agent stopped early, or "
                "it only explained what it would do."
            ),
        ),
    ),
    "task_done": Noul(
        instructions=_CONTEXT
        + "`task` and `earlier` say what the conversation as a whole is for. "
        "Is all of it done now, including parts planned for later, with no "
        "step the agent promised still pending? Doing one part now, as "
        "asked, leaves the parts planned for later open.",
        criteria=NoulCriteria(
            true=(
                "Nothing from the task or earlier requests is left undone, or "
                "the user dropped what is left."
            ),
            false=(
                "A part of the task, even one planned for later, an earlier "
                "request, or a step the agent said it would take next is "
                "still open."
            ),
        ),
    ),
    "asks_user": Noul(
        instructions=_CONTEXT
        + "Does `reply` ask the user a substantive question, one the agent "
        "needs answered to go on, even when worded without a question mark?",
        criteria=NoulCriteria(
            true=(
                "It asks for information, a preference or a decision the "
                "agent cannot make on its own."
            ),
            false=(
                "It asks nothing, or only offers more help or to continue "
                '("Anything else?", "Tell me when you\'re ready for the '
                'next one").'
            ),
        ),
    ),
    "hands_off_review": Noul(
        instructions=_CONTEXT
        + "Does `reply` hand the user a change to review, approve or merge, "
        "such as a pull request that is ready for review?",
        criteria=NoulCriteria(
            true="The reply waits for the user to review, approve or merge.",
            false=(
                "Nothing is handed over, or the user already said they will "
                "take it from here or that nothing more is needed."
            ),
        ),
    ),
    "offers_choice": Noul(
        instructions=_CONTEXT
        + "Does `reply` lay out options and leave the choice to the user?",
    ),
    "asks_to_check": Noul(
        instructions=_CONTEXT
        + "Does `reply` ask the user to try, test or verify something "
        "themselves?",
    ),
    "reports_problem": Noul(
        instructions=_CONTEXT
        + "Does `reply` report a failure, an error or a blocker that is still "
        "unresolved?",
    ),
}

# What the user owes when each hand-off judgment holds.
_OWED_BY_JUDGMENT: Final[dict[str, Owed]] = {
    "asks_user": "answer",
    "hands_off_review": "review",
    "offers_choice": "decide",
    "asks_to_check": "check",
    "reports_problem": "check",
}

# Hand-offs that wait on the user, so the conversation needs them. A report
# of a problem alone does not ask anything of the user.
_NEEDS_PERSON: Final[frozenset[str]] = frozenset(
    {"asks_user", "hands_off_review", "offers_choice", "asks_to_check"}
)


class ResolutionState(BaseModel):
    """What the judge sees of one turn."""

    request: str | None = Field(
        default=None, description="The user's message that started the turn."
    )
    reply: str | None = Field(default=None, description="The agent's final message.")
    actions: list[str] = Field(
        default_factory=list, description="Short summaries of the agent's actions."
    )
    task: str | None = Field(
        default=None, description="The message the conversation started with."
    )
    earlier: list[str] = Field(
        default_factory=list,
        description="The user's requests between `task` and `request`.",
    )

    def as_state(self) -> dict[str, Any]:
        return {
            "task": _clip(self.task, _CONTEXT_TEXT_LIMIT),
            "earlier": [
                _clip(text, _CONTEXT_TEXT_LIMIT)
                for text in self.earlier[-EARLIER_REQUESTS_KEPT:]
            ],
            "request": _clip(self.request),
            "reply": _clip(self.reply),
            "actions": self.actions[-_ACTIONS_LIMIT:],
        }


class ResolutionVerdict(BaseModel):
    """The judge's answer for one turn."""

    probability: float = Field(
        ge=0.0, le=1.0, description="Probability that nothing is left for the user."
    )
    owed: Owed = Field(
        description=(
            "Most likely thing the user still owes; `unfinished` when work "
            "is left but nothing waits on the user."
        )
    )
    judgments: dict[str, float] = Field(
        default_factory=dict, description="Each judgment's probability, by name."
    )
    model: str | None = Field(default=None, description="The model that answered.")

    @property
    def resolved(self) -> bool:
        return self.owed == "nothing"

    @property
    def needs_user(self) -> bool:
        """The reply waits on the user: a question, a choice, a review or a
        check of theirs."""
        return any(self.judgments.get(name, 0.0) >= LIKELY for name in _NEEDS_PERSON)

    def note(self) -> str:
        """One short line for the board: ``0.93 nothing`` style."""
        return f"{self.probability:.2f} {self.owed}"


def verdict_from_judgments(
    judgments: Mapping[str, float], model: str | None = None
) -> ResolutionVerdict:
    """Combine the judgments into a verdict, in code.

    A turn is resolved when the request and the whole task are done (both at
    least :data:`AUTO_RESOLVE_PROBABILITY`) and every hand-off stays below
    :data:`HAND_OFF_CEILING`. ``probability`` is the weakest of those
    signals: ``min(handled, task_done, 1 - strongest hand-off)``. Otherwise
    the user owes the strongest hand-off when it holds; ``unfinished`` when
    work is left that waits on no one; ``unclear`` when only a weak hand-off
    stands in the way.
    """
    done = min(_unit(judgments.get("handled")), _unit(judgments.get("task_done")))
    name = max(_OWED_BY_JUDGMENT, key=lambda n: _unit(judgments.get(n)))
    hand_off = _unit(judgments.get(name))
    owed: Owed
    if hand_off >= LIKELY:
        owed = _OWED_BY_JUDGMENT[name]
    elif done < AUTO_RESOLVE_PROBABILITY:
        owed = "unfinished"
    elif hand_off >= HAND_OFF_CEILING:
        owed = "unclear"
    else:
        owed = "nothing"
    return ResolutionVerdict(
        probability=min(done, 1.0 - hand_off),
        owed=owed,
        judgments={n: _unit(p) for n, p in judgments.items()},
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

    For people without a decision model: the user's own LLM answers the same
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
    "You judge conversations between a user and a coding agent. Answer each "
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


def _clip(text: str | None, limit: int = _TEXT_LIMIT) -> str | None:
    if text is None or len(text) <= limit:
        return text
    # Keep the end of a long text: a reply says there how the turn ended.
    return "…" + text[-(limit - 1) :]
