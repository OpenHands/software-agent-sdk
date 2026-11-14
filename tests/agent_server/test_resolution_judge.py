"""Tests for how the server picks and limits the resolution judge's model."""

import pytest
from pydantic import SecretStr

from openhands.agent_server import resolution_judge
from openhands.sdk.conversation.resolution import (
    LLMDecisionModel,
    ResolutionState,
    ResolutionVerdict,
)
from openhands.sdk.llm import LLM
from openhands.sdk.llm.decision import DecisionModel


@pytest.mark.asyncio
async def test_fallback_judge_uses_a_limited_copy_of_the_conversation_llm(
    monkeypatch,
):
    monkeypatch.delenv(resolution_judge.MODEL_ENV, raising=False)
    seen: list[DecisionModel] = []

    async def fake_judge(model, state):
        seen.append(model)
        return ResolutionVerdict(probability=1.0, owed="nothing")

    monkeypatch.setattr(resolution_judge, "judge_resolution", fake_judge)
    llm = LLM(
        model="openai/gpt-4o",
        api_key=SecretStr("k"),
        usage_id="agent",
        timeout=600,
        num_retries=8,
    )

    judge = resolution_judge.make_resolution_judge(lambda: llm)
    await judge(ResolutionState(reply="Done."))

    model = seen[0]
    assert isinstance(model, LLMDecisionModel)
    assert model.llm.timeout == 60
    assert model.llm.num_retries == 0
    assert model.llm.model == "openai/gpt-4o"
    assert (llm.timeout, llm.num_retries) == (600, 8)


@pytest.mark.asyncio
async def test_judge_is_off_when_configured_off(monkeypatch):
    monkeypatch.setenv(resolution_judge.MODEL_ENV, "off")
    judge = resolution_judge.make_resolution_judge(lambda: None)

    assert await judge(ResolutionState(reply="Done.")) is None
