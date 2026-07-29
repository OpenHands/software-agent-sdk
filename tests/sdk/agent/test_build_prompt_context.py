"""Tests for ``AgentBase._build_prompt_context()``.

``test_equivalent_to_static_template_kwargs`` captures the kwargs
``static_system_message`` produces and asserts the snapshot reproduces them.
"""

import openhands.sdk.agent.base as agent_base
from openhands.sdk import Agent
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.context.prompts.section import Platform, PromptContext
from openhands.sdk.llm import LLM
from openhands.sdk.skills import Skill
from openhands.sdk.tool import Tool


def _make_llm(model: str = "test-model") -> LLM:
    return LLM(model=model, usage_id="test-llm")


def test_returns_prompt_context() -> None:
    agent = Agent(llm=_make_llm(), tools=[])
    assert isinstance(agent._build_prompt_context(), PromptContext)


def test_equivalent_to_static_template_kwargs(monkeypatch) -> None:
    # After the Phase 3 cutover the default prompt renders via the registry, so
    # render_template is only reached through the custom-filename escape hatch.
    # That path and _build_prompt_context both resolve kwargs via
    # _resolved_template_kwargs, so they must not drift.
    agent = Agent(
        llm=_make_llm(),
        tools=[Tool(name="browser_tool_set")],
        system_prompt_kwargs={"cli_mode": True},
        system_prompt_filename="custom.j2",
    )

    captured: dict = {}

    def _capture(*, prompt_dir, template_name, **kwargs):
        captured.update(kwargs)
        return ""

    monkeypatch.setattr(agent_base, "render_template", _capture)
    _ = agent.static_system_message  # populates `captured`

    ctx = agent._build_prompt_context()
    assert ctx.template_kwargs == captured


def test_browser_auto_detected() -> None:
    agent = Agent(llm=_make_llm(), tools=[Tool(name="browser_tool_set")])
    assert agent._build_prompt_context().enable_browser is True


def test_browser_absent_without_tool() -> None:
    agent = Agent(llm=_make_llm(), tools=[Tool(name="terminal_tool")])
    assert agent._build_prompt_context().enable_browser is False


def test_browser_explicit_override_wins() -> None:
    agent = Agent(
        llm=_make_llm(),
        tools=[Tool(name="browser_tool_set")],
        system_prompt_kwargs={"enable_browser": False},
    )
    assert agent._build_prompt_context().enable_browser is False


def test_model_family_from_spec() -> None:
    agent = Agent(llm=_make_llm(model="claude-sonnet-4-5"), tools=[])
    assert agent._build_prompt_context().model_family == "anthropic_claude"


def test_template_kwargs_passthrough_includes_security_analyzer_and_cli_mode() -> None:
    agent = Agent(llm=_make_llm(), tools=[], system_prompt_kwargs={"cli_mode": True})
    ctx = agent._build_prompt_context()
    # Agent injects llm_security_analyzer=True as a default kwarg.
    assert ctx.template_kwargs.get("llm_security_analyzer") is True
    assert ctx.cli_mode is True


def test_tool_names_snapshot() -> None:
    agent = Agent(
        llm=_make_llm(),
        tools=[Tool(name="terminal_tool"), Tool(name="browser_tool_set")],
    )
    assert agent._build_prompt_context().tool_names == (
        "terminal_tool",
        "browser_tool_set",
    )


def test_working_dir_is_none_in_bridge() -> None:
    agent = Agent(llm=_make_llm(), tools=[])
    assert agent._build_prompt_context().working_dir is None


def test_platform_is_snapshot() -> None:
    agent = Agent(llm=_make_llm(), tools=[])
    assert agent._build_prompt_context().platform is Platform.current()


def test_dynamic_snapshot_from_agent_context() -> None:
    agent = Agent(
        llm=_make_llm(),
        tools=[],
        agent_context=AgentContext(secrets={"API_TOKEN": "shh"}),
    )
    ctx = agent._build_prompt_context()
    assert ctx.secret_names == ("API_TOKEN",)
    assert ctx.now is not None  # AgentContext defaults current_datetime


def test_no_agent_context_yields_empty_dynamic_fields() -> None:
    agent = Agent(llm=_make_llm(), tools=[])
    ctx = agent._build_prompt_context()
    assert ctx.skill_names == ()
    assert ctx.secret_names == ()
    assert ctx.now is None


def test_explicit_model_family_kwarg_wins_over_spec() -> None:
    """A caller-supplied model_family is not overwritten by the auto-detected one."""
    agent = Agent(
        llm=_make_llm(model="claude-sonnet-4-5"),
        tools=[],
        system_prompt_kwargs={"model_family": "custom"},
    )
    assert agent._build_prompt_context().model_family == "custom"


def test_model_variant_from_spec() -> None:
    agent = Agent(llm=_make_llm(model="gpt-5-codex"), tools=[])
    ctx = agent._build_prompt_context()
    assert ctx.model_family == "openai_gpt"
    assert ctx.template_kwargs["model_variant"] == "gpt-5-codex"


def test_skill_names_snapshot_from_agent_context() -> None:
    agent = Agent(
        llm=_make_llm(),
        tools=[],
        agent_context=AgentContext(
            skills=[Skill(name="demo", content="x", source="s.md", trigger=None)]
        ),
    )
    assert agent._build_prompt_context().skill_names == ("demo",)
