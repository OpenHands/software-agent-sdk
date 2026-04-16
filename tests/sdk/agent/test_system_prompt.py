"""Tests for the system_prompt inline override on Agent / AgentBase."""

from __future__ import annotations

import pytest

from openhands.sdk.agent import Agent
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.llm import LLM


def _make_llm() -> LLM:
    return LLM(model="test-model", usage_id="test")


# --- construction ---


def test_system_prompt_is_accepted_and_stored() -> None:
    agent = Agent(llm=_make_llm(), tools=[], system_prompt="CUSTOM")
    assert agent.system_prompt == "CUSTOM"


def test_system_prompt_defaults_to_none() -> None:
    agent = Agent(llm=_make_llm(), tools=[])
    assert agent.system_prompt is None


# --- static_system_message uses inline prompt ---


def test_static_system_message_returns_inline_prompt() -> None:
    agent = Agent(llm=_make_llm(), tools=[], system_prompt="MY PROMPT")
    assert agent.static_system_message == "MY PROMPT"


def test_static_system_message_falls_back_to_template_when_none() -> None:
    agent = Agent(llm=_make_llm(), tools=[])
    # The default template renders a non-empty string
    assert len(agent.static_system_message) > 0
    assert agent.static_system_message != ""


# --- mutual-exclusivity validation ---


def test_system_prompt_and_custom_filename_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="Cannot set both"):
        Agent(
            llm=_make_llm(),
            tools=[],
            system_prompt="inline",
            system_prompt_filename="custom.j2",
        )


def test_system_prompt_with_default_filename_is_ok() -> None:
    """system_prompt + the default filename should be accepted."""
    agent = Agent(
        llm=_make_llm(),
        tools=[],
        system_prompt="inline",
        system_prompt_filename="system_prompt.j2",
    )
    assert agent.system_prompt == "inline"
    assert agent.static_system_message == "inline"


# --- serialization round-trip ---


def test_system_prompt_survives_json_round_trip() -> None:
    agent = Agent(llm=_make_llm(), tools=[], system_prompt="ROUND TRIP")
    agent_json = agent.model_dump_json()
    restored = AgentBase.model_validate_json(agent_json)
    assert isinstance(restored, Agent)
    assert restored.system_prompt == "ROUND TRIP"
    assert restored.static_system_message == "ROUND TRIP"


def test_system_prompt_none_survives_json_round_trip() -> None:
    agent = Agent(llm=_make_llm(), tools=[])
    agent_json = agent.model_dump_json()
    restored = AgentBase.model_validate_json(agent_json)
    assert isinstance(restored, Agent)
    assert restored.system_prompt is None
