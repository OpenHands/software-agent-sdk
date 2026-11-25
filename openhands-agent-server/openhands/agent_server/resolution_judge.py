"""The judge that decides whether a project conversation is resolved.

It asks the typed questions in :mod:`openhands.sdk.conversation.resolution`
of the saved ``resolution_judge`` LLM profile, which is created on first use
as a copy of the conversation's own LLM (see
:func:`~openhands.sdk.conversation.resolution.resolution_judge`).
"""

from collections.abc import Awaitable, Callable

from openhands.agent_server.persistence import ServerStores
from openhands.agent_server.persistence.async_store import run_store
from openhands.agent_server.project_board import ResolutionJudge
from openhands.sdk.conversation.resolution import (
    ResolutionState,
    ResolutionVerdict,
    judge_resolution,
    resolution_judge,
)
from openhands.sdk.llm import LLM


ConversationContext = Callable[[], Awaitable[tuple[str | None, list[str]]]]
"""Reads a conversation's first message and the user's requests since,
oldest first, for :attr:`ResolutionState.task` and ``earlier``."""


def make_resolution_judge(
    agent_llm: Callable[[], LLM | None],
    stores: Callable[[], ServerStores | None],
    context: ConversationContext | None = None,
) -> ResolutionJudge:
    """A judge for one conversation; ``agent_llm`` returns its live LLM (the
    profile's default when there is none yet), ``stores`` the server's
    current stores, and ``context`` what the conversation was asked to do
    before this turn."""

    async def judge(state: ResolutionState) -> ResolutionVerdict | None:
        llm = agent_llm()
        server_stores = stores()
        if llm is None or server_stores is None:
            return None
        model = await run_store(resolution_judge, llm, server_stores.llm_profiles)
        if context is not None:
            task, earlier = await context()
            state = state.model_copy(update={"task": task, "earlier": earlier})
        return await judge_resolution(model, state)

    return judge
