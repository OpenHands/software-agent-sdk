"""Pick the model that judges whether a project conversation is resolved.

The judge asks the typed questions in
:mod:`openhands.sdk.conversation.resolution` of:

1. the decision model in ``OH_RESOLUTION_MODEL``, when set: a LiteLLM model
   string such as ``typesafe/jev-latest`` or ``hosted_vllm/<model>`` for an
   open model served by vLLM, with ``OH_RESOLUTION_API_KEY`` (or
   ``JEV_API_KEY``) and ``OH_RESOLUTION_BASE_URL``;
2. otherwise the conversation's own LLM, through
   :class:`~openhands.sdk.conversation.resolution.LLMDecisionModel`.

``OH_RESOLUTION_MODEL=off`` turns the judge off; a confident critic score
still resolves a conversation then.
"""

import os
from collections.abc import Awaitable, Callable
from typing import Final

from openhands.agent_server.project_board import ResolutionJudge
from openhands.sdk.conversation.resolution import (
    LLMDecisionModel,
    ResolutionState,
    ResolutionVerdict,
    judge_resolution,
)
from openhands.sdk.llm import LLM
from openhands.sdk.llm.decision import DecisionModel


MODEL_ENV: Final[str] = "OH_RESOLUTION_MODEL"
API_KEY_ENV: Final[str] = "OH_RESOLUTION_API_KEY"
BASE_URL_ENV: Final[str] = "OH_RESOLUTION_BASE_URL"
JEV_API_KEY_ENV: Final[str] = "JEV_API_KEY"
OFF: Final[str] = "off"

# A judge that is slow or down must not hold the board's tag writes for long.
_TIMEOUT_SECONDS: Final[int] = 60
_RETRIES: Final[int] = 1


def decision_model_from_env() -> DecisionModel | None:
    model = os.getenv(MODEL_ENV, "").strip()
    if not model or model == OFF:
        return None
    return DecisionModel(
        model=model,
        api_key=os.getenv(API_KEY_ENV) or os.getenv(JEV_API_KEY_ENV) or None,
        base_url=os.getenv(BASE_URL_ENV) or None,
        timeout=_TIMEOUT_SECONDS,
        num_retries=_RETRIES,
    )


ConversationContext = Callable[[], Awaitable[tuple[str | None, list[str]]]]
"""Reads a conversation's first message and the user's requests since,
oldest first, for :attr:`ResolutionState.task` and ``earlier``."""


def make_resolution_judge(
    agent_llm: Callable[[], LLM | None],
    context: ConversationContext | None = None,
) -> ResolutionJudge:
    """A judge for one conversation; ``agent_llm`` returns its live LLM and
    ``context`` what the conversation was asked to do before this turn."""

    async def judge(state: ResolutionState) -> ResolutionVerdict | None:
        if os.getenv(MODEL_ENV, "").strip() == OFF:
            return None
        if context is not None:
            task, earlier = await context()
            state = state.model_copy(update={"task": task, "earlier": earlier})
        model = decision_model_from_env()
        if model is None:
            llm = agent_llm()
            if llm is None:
                return None
            # A copy with the judge's own limits: the conversation LLM's
            # timeout and retries would nest inside the judge's retry loop.
            judge_llm = llm.model_copy(
                update={"timeout": _TIMEOUT_SECONDS, "num_retries": 0}
            )
            model = LLMDecisionModel(
                llm=judge_llm, timeout=_TIMEOUT_SECONDS, num_retries=_RETRIES
            )
        return await judge_resolution(model, state)

    return judge
