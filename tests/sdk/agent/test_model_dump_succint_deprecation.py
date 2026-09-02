"""Deprecation coverage for AgentBase.model_dump_succint (issue #4224)."""

import pytest
from deprecation import DeprecatedWarning

from openhands.sdk.agent import Agent
from openhands.sdk.llm import LLM


def _agent() -> Agent:
    return Agent(llm=LLM(model="test-model", usage_id="test-llm"), tools=[])


def test_model_dump_succint_emits_deprecation_warning() -> None:
    """The method warns with the scheduled 1.40.0 -> 1.45.0 runway."""
    with pytest.warns(DeprecatedWarning, match="model_dump_succint") as caught:
        _agent().model_dump_succint()

    message = str(caught[0].message)
    assert "deprecated as of 1.40.0" in message
    assert "removed in 1.45.0" in message


def test_model_dump_succint_matches_model_dump_exclude_none() -> None:
    """The only documented difference from model_dump is exclude_none=True."""
    agent = _agent()
    with pytest.warns(DeprecatedWarning):
        dumped = agent.model_dump_succint()

    assert dumped == agent.model_dump(exclude_none=True)


def test_model_dump_succint_honors_explicit_exclude_none_false() -> None:
    """Callers can still override the exclude_none default."""
    agent = _agent()
    with pytest.warns(DeprecatedWarning):
        dumped = agent.model_dump_succint(exclude_none=False)

    assert dumped == agent.model_dump()
