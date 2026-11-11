"""Live evaluation of the resolution judge on real turns.

Each case is one finished turn of a project conversation and what the board
should make of it: ``resolved`` (nothing left for the person), ``needs_person``
(the person owes an answer, a review, a choice or a check), or ``open``
(neither: work is left, but not the person's).

The cases run only against a real model:

- ``OH_RESOLUTION_MODEL`` (with ``OH_RESOLUTION_API_KEY`` or ``JEV_API_KEY``,
  and ``OH_RESOLUTION_BASE_URL``): a decision model, as the server uses it,
  e.g. ``typesafe/jev-latest``.
- ``RESOLUTION_EVAL_LLM`` (with ``RESOLUTION_EVAL_API_KEY``): a chat model
  answering the same questions, as the server's fallback does, e.g.
  ``deepseek/deepseek-flash``.

Run with ``uv run pytest tests/agent_server/test_resolution_eval.py -v``.

The person's "merged" after a pull request is a case twice. A worker should
resolve such a conversation itself with ``resolve_conversation``, and then
the judge does not run at all (``test_project_board`` pins that). The cases
here are for the worker that only reacts and ends its turn.
"""

import os
from typing import Literal

import pytest
from pydantic import SecretStr

from openhands.agent_server.resolution_judge import decision_model_from_env
from openhands.sdk.conversation.resolution import (
    LLMDecisionModel,
    ResolutionState,
    ResolutionVerdict,
    judge_resolution,
)
from openhands.sdk.llm import LLM
from openhands.sdk.llm.decision import DecisionModel


Expected = Literal["resolved", "needs_person", "open"]

_PR_TASK = (
    "The Safari export button does nothing. Fix it and open a PR in acme/web-app."
)

CASES: dict[str, tuple[ResolutionState, Expected]] = {
    "merged, worker reacted": (
        ResolutionState(
            task=_PR_TASK,
            request="merged",
            reply="",
            actions=["react: :tada: on the person's message"],
        ),
        "resolved",
    ),
    "merged, worker thanked": (
        ResolutionState(
            task=_PR_TASK,
            request="merged",
            reply="Great, thanks for merging! 🎉",
            actions=["react: :tada: on the person's message"],
        ),
        "resolved",
    ),
    "answered question": (
        ResolutionState(
            request="What does classify_turn do in project_board.py?",
            reply=(
                "`classify_turn` looks at the last paragraph of the agent's "
                "final message, outside code blocks. If a sentence there ends "
                "with a question mark, the turn counts as `asked`; otherwise "
                "`answered`."
            ),
            actions=["terminal: grep classify_turn"],
        ),
        "resolved",
    ),
    "pleasantry after an answer": (
        ResolutionState(
            request="What is 17 times 23?",
            reply="17 times 23 is 391. Anything else I can help with?",
        ),
        "resolved",
    ),
    "done, committed to main": (
        ResolutionState(
            request=(
                "Add a page about reactions to my notebook site and push it to main."
            ),
            reply=(
                "Added arch/conversation-reactions.html, linked it from the "
                "homepage and pushed to main (abc1234)."
            ),
            actions=["terminal: git push origin main"],
        ),
        "resolved",
    ),
    "person takes the merge": (
        ResolutionState(
            task="Split the renderer refactor into branches and open them.",
            earlier=["Should they be merged together?"],
            request=(
                "Merge separately is fine; I'll merge the three branches "
                "myself. Nothing more needed here."
            ),
            reply=(
                "Understood. The three branches are pushed and ready for you "
                "to merge when you get to it."
            ),
        ),
        "resolved",
    ),
    "PR waiting for review": (
        ResolutionState(
            task=_PR_TASK,
            request=_PR_TASK,
            reply=(
                "I fixed the handler and opened PR #212. CI is green. It's "
                "ready for your review."
            ),
            actions=["terminal: gh pr create"],
        ),
        "needs_person",
    ),
    "asks a choice": (
        ResolutionState(
            request="Suggest two names for my new cat, with one line on each.",
            reply=(
                "**Mochi**: soft and cozy.\n\n**Juniper**: a little "
                "adventurous.\n\nTell me which one you prefer."
            ),
        ),
        "needs_person",
    ),
    "second haiku still to write": (
        ResolutionState(
            task=(
                "We will write two haiku: one about rain, then one about "
                "snow. Write only the rain haiku now, and finish."
            ),
            request=(
                "We will write two haiku: one about rain, then one about "
                "snow. Write only the rain haiku now, and finish."
            ),
            reply=(
                "Soft rain on the roof\nwhispers to the sleeping street\n"
                "puddles hold the sky"
            ),
        ),
        "open",
    ),
}


def _model() -> DecisionModel | None:
    model = decision_model_from_env()
    if model is not None:
        return model
    name = os.getenv("RESOLUTION_EVAL_LLM", "").strip()
    if not name:
        return None
    key = os.getenv("RESOLUTION_EVAL_API_KEY")
    llm = LLM(
        model=name,
        api_key=SecretStr(key) if key else None,
        usage_id="resolution-eval",
        num_retries=0,
    )
    return LLMDecisionModel(llm=llm, timeout=60, num_retries=1)


def _outcome(verdict: ResolutionVerdict) -> Expected:
    if verdict.needs_person:
        return "needs_person"
    return "resolved" if verdict.resolved else "open"


@pytest.mark.skipif(
    _model() is None,
    reason="needs OH_RESOLUTION_MODEL or RESOLUTION_EVAL_LLM (a live model)",
)
@pytest.mark.parametrize("name", list(CASES))
@pytest.mark.asyncio
async def test_resolution_eval(name: str):
    state, expected = CASES[name]
    model = _model()
    assert model is not None

    verdict = await judge_resolution(model, state)

    assert _outcome(verdict) == expected, f"{name}: {verdict.note()} " + " ".join(
        f"{k}={p:.2f}" for k, p in verdict.judgments.items()
    )
