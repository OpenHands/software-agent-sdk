"""Tests for the system_prompt inline override on Agent / AgentBase."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest

from openhands.sdk.agent import Agent
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.context.prompts.presets import PromptPreset, create_registry
from openhands.sdk.llm import LLM, TextContent
from openhands.sdk.security.risk import CLI_TIERS, SANDBOX_TIERS


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


# --- risk guidance under custom vs default prompts (#5444) ---


@pytest.mark.parametrize(
    "cli_mode, expected_tier_snippet, expected_tiers_block",
    [
        (True, "**LOW**: Safe, read-only actions.", CLI_TIERS),
        (False, "**LOW**: Read-only actions inside sandbox", SANDBOX_TIERS),
    ],
    ids=["cli_mode", "sandbox_mode"],
)
def test_custom_system_prompt_restores_risk_guidance(
    cli_mode: bool,
    expected_tier_snippet: str,
    expected_tiers_block: str,
) -> None:
    """When a custom system prompt replaces the static prompt:
    1. Static message contains only the custom prompt verbatim.
    2. Dynamic context contains escalation rules in <SECURITY_RISK_ASSESSMENT>.
    3. security_risk_description contains concise tier definitions.
    """
    from openhands.sdk.security.risk import get_security_risk_description

    agent = Agent(
        llm=_make_llm(),
        tools=[],
        system_prompt="You are a custom security-focused coding assistant.",
        system_prompt_kwargs={"cli_mode": cli_mode},
    )

    # 1. Static prompt is verbatim custom prompt; no risk assessment block
    assert agent.static_system_message == (
        "You are a custom security-focused coding assistant."
    )
    assert "<SECURITY_RISK_ASSESSMENT>" not in agent.static_system_message

    # 2. Dynamic context carries the non-overridable escalation rules
    dynamic = agent.dynamic_context or ""
    assert "<SECURITY_RISK_ASSESSMENT>" in dynamic
    assert "**Global Rules**" in dynamic
    assert "**Repository Context Supply Chain Rules**" in dynamic
    assert "<UNTRUSTED_CONTENT>" in dynamic
    # Escalation block does not duplicate tier definitions
    assert expected_tiers_block not in dynamic

    # 3. security_risk_description has the appropriate tiers
    expected_desc = get_security_risk_description(cli_mode=cli_mode)
    assert agent.security_risk_description == expected_desc
    assert agent.security_risk_description is not None
    assert expected_tier_snippet in agent.security_risk_description


def test_default_prompt_leaves_security_risk_description_none() -> None:
    """Without a custom system prompt:
    1. <SECURITY_RISK_ASSESSMENT> is in static_system_message with tiers + rules.
    2. Dynamic context does not contain <SECURITY_RISK_ASSESSMENT>.
    3. security_risk_description is None.
    """
    from openhands.sdk.security.risk import CLI_TIERS

    agent = Agent(llm=_make_llm(), tools=[])

    # 1. Static prompt has full risk assessment
    assert "<SECURITY_RISK_ASSESSMENT>" in agent.static_system_message
    assert CLI_TIERS in agent.static_system_message
    assert "**Global Rules**" in agent.static_system_message

    # 2. Dynamic context has no security risk section
    assert "<SECURITY_RISK_ASSESSMENT>" not in (agent.dynamic_context or "")

    # 3. Description is None to avoid duplication
    assert agent.security_risk_description is None


@pytest.mark.parametrize(
    "has_custom_prompt",
    [True, False],
    ids=["custom_prompt", "default_prompt"],
)
def test_analyzer_off_disables_security_risk_guidance(
    has_custom_prompt: bool,
) -> None:
    """When llm_security_analyzer is False:
    1. Neither static nor dynamic prompts have <SECURITY_RISK_ASSESSMENT>.
    2. security_risk_description is None.
    """
    kwargs: dict[str, Any] = {
        "llm": _make_llm(),
        "tools": [],
        "system_prompt_kwargs": {"llm_security_analyzer": False},
    }
    if has_custom_prompt:
        kwargs["system_prompt"] = "Custom prompt."

    agent = Agent(**kwargs)

    assert "<SECURITY_RISK_ASSESSMENT>" not in agent.static_system_message
    assert "<SECURITY_RISK_ASSESSMENT>" not in (agent.dynamic_context or "")
    assert agent.security_risk_description is None


def test_init_state_emits_dynamic_escalation_rules(tmp_path: Path) -> None:
    """Integration check: Agent.init_state emits SystemPromptEvent with
    escalation rules in dynamic_context when custom prompt is set,
    and events_to_messages compiles them into the system message.
    """
    import uuid

    from openhands.sdk.conversation.state import ConversationState
    from openhands.sdk.event import LLMConvertibleEvent, SystemPromptEvent
    from openhands.sdk.workspace.local import LocalWorkspace

    agent = Agent(
        llm=_make_llm(),
        tools=[],
        system_prompt="Custom static prompt text.",
    )
    state = ConversationState.create(
        id=uuid.uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
    )

    emitted: list[SystemPromptEvent] = []
    agent.init_state(
        state,
        on_event=lambda e: (
            emitted.append(e) if isinstance(e, SystemPromptEvent) else None
        ),
    )

    assert len(emitted) == 1
    system_event = emitted[0]
    assert system_event.system_prompt.text == "Custom static prompt text."
    assert system_event.dynamic_context is not None
    assert "<SECURITY_RISK_ASSESSMENT>" in system_event.dynamic_context.text
    assert "**Global Rules**" in system_event.dynamic_context.text

    messages = LLMConvertibleEvent.events_to_messages([system_event])
    assert len(messages) == 1
    sys_msg = messages[0]
    assert sys_msg.role == "system"
    assert len(sys_msg.content) == 2
    c0 = sys_msg.content[0]
    assert isinstance(c0, TextContent)
    assert c0.text == "Custom static prompt text."
    c1 = sys_msg.content[1]
    assert isinstance(c1, TextContent)
    assert "<SECURITY_RISK_ASSESSMENT>" in c1.text
