"""Tests for how the server runs the resolution judge on its profile."""

from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from openhands.agent_server import resolution_judge
from openhands.sdk.conversation.resolution import (
    RESOLUTION_JUDGE_PROFILE_NAME,
    LLMDecisionModel,
    ResolutionState,
    ResolutionVerdict,
)
from openhands.sdk.llm import LLM
from openhands.sdk.llm.decision import DecisionModel
from openhands.sdk.llm.llm_profile_store import LLMProfileStore


def _stores(tmp_path):
    return SimpleNamespace(llm_profiles=LLMProfileStore(tmp_path), cipher=None)


@pytest.mark.asyncio
async def test_the_judge_runs_on_a_limited_copy_of_its_saved_profile(
    monkeypatch, tmp_path
):
    seen: list[DecisionModel] = []

    async def fake_judge(model, state):
        seen.append(model)
        return ResolutionVerdict(probability=1.0, owed="nothing")

    monkeypatch.setattr(resolution_judge, "judge_resolution", fake_judge)
    stores = _stores(tmp_path)
    llm = LLM(
        model="openai/gpt-4o",
        api_key=SecretStr("k"),
        usage_id="agent",
        timeout=600,
        num_retries=8,
    )

    judge = resolution_judge.make_resolution_judge(lambda: llm, lambda: stores)  # type: ignore[arg-type]
    await judge(ResolutionState(reply="Done."))

    model = seen[0]
    assert isinstance(model, LLMDecisionModel)
    assert model.llm.timeout == 60
    assert model.llm.num_retries == 0
    assert model.llm.model == "openai/gpt-4o"
    assert (llm.timeout, llm.num_retries) == (600, 8)
    # The profile was created from the conversation's LLM, for the user to
    # find with their other profiles.
    assert stores.llm_profiles.load(RESOLUTION_JUDGE_PROFILE_NAME).model == (
        "openai/gpt-4o"
    )


@pytest.mark.asyncio
async def test_the_judge_does_nothing_without_a_conversation_llm(tmp_path):
    stores = _stores(tmp_path)
    judge = resolution_judge.make_resolution_judge(lambda: None, lambda: stores)  # type: ignore[arg-type]

    assert await judge(ResolutionState(reply="Done.")) is None
    assert stores.llm_profiles.list() == []
