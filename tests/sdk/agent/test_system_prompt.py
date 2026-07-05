"""Tests for the system_prompt inline override on Agent / AgentBase."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from openhands.sdk.agent import Agent
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.context.prompts.presets import PromptPreset, create_registry
from openhands.sdk.llm import LLM


def _make_llm() -> LLM:
    return LLM(model="test-model", usage_id="test")


class _CustomPromptDirAgent(Agent):
    """Agent subclass whose ``prompt_dir`` points at a per-test directory."""

    custom_prompt_dir: ClassVar[str] = ""

    @property
    def prompt_dir(self) -> str:
        return type(self).custom_prompt_dir


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


# --- custom prompt_dir escape hatch (registry cutover) ---


def test_subclass_default_named_template_renders_through_jinja(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A subclass shipping its own default-named template renders it, not the
    registry prompt."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "system_prompt.j2").write_text(
        "CUSTOM SUBCLASS PROMPT", encoding="utf-8"
    )
    monkeypatch.setattr(_CustomPromptDirAgent, "custom_prompt_dir", str(prompts))

    agent = _CustomPromptDirAgent(llm=_make_llm(), tools=[])
    assert agent.static_system_message == "CUSTOM SUBCLASS PROMPT"


def test_builtin_default_prompt_uses_registry() -> None:
    """The built-in prompt dir + default filename still routes through the registry."""
    agent = Agent(llm=_make_llm(), tools=[])
    expected = create_registry().build(agent._build_prompt_context()).static
    assert agent.static_system_message == expected


# --- planning preset (sentinel-filename routing) ---


def test_prompt_preset_resolves_from_builtin_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Built-in sentinel filenames map to a preset; anything else escapes to Jinja."""
    assert Agent(llm=_make_llm(), tools=[])._prompt_preset is PromptPreset.DEFAULT
    planning = Agent(
        llm=_make_llm(),
        tools=[],
        system_prompt_filename="system_prompt_planning.j2",
        system_prompt_kwargs={"plan_structure": "X"},
    )
    assert planning._prompt_preset is PromptPreset.PLANNING
    # A non-sentinel filename takes the Jinja escape hatch (preset None).
    assert (
        Agent(
            llm=_make_llm(), tools=[], system_prompt_filename="custom.j2"
        )._prompt_preset
        is None
    )
    # So does a subclass with its own prompt_dir, even with a sentinel filename.
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    monkeypatch.setattr(_CustomPromptDirAgent, "custom_prompt_dir", str(prompts))
    sub = _CustomPromptDirAgent(
        llm=_make_llm(), tools=[], system_prompt_filename="system_prompt_planning.j2"
    )
    assert sub._prompt_preset is None


def test_planning_filename_routes_through_registry() -> None:
    """The planning sentinel renders the planning registry preset, not the default."""
    agent = Agent(
        llm=_make_llm(),
        tools=[],
        system_prompt_filename="system_prompt_planning.j2",
        system_prompt_kwargs={"plan_structure": "1. OBJECTIVE"},
    )
    static = agent.static_system_message
    expected = (
        create_registry(PromptPreset.PLANNING)
        .build(agent._build_prompt_context())
        .static
    )
    assert static == expected
    assert static.startswith("You are a Planning Agent")
    # Standalone composition: the default OpenHands identity must not leak in.
    assert "<SOUL>" not in static


def test_custom_security_policy_is_resolved_by_registry(tmp_path: Path) -> None:
    """A custom security_policy_filename is honored via the registry (no Jinja escape
    hatch), and the built-in default policy does not leak in alongside it."""
    policy = tmp_path / "custom_policy.j2"
    policy.write_text("CUSTOM POLICY", encoding="utf-8")

    agent = Agent(llm=_make_llm(), tools=[], security_policy_filename=str(policy))
    static = agent.static_system_message

    assert "CUSTOM POLICY" in static
    # The default policy must NOT leak in alongside the custom one.
    assert "🔐 Security Policy" not in static


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
