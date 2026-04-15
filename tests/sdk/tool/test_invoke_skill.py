"""Tests for the `invoke_skill` built-in tool."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext
from openhands.sdk.context import KeywordTrigger
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.skills import Skill
from openhands.sdk.tool.builtins import (
    BUILT_IN_TOOL_CLASSES,
    BUILT_IN_TOOLS,
    InvokeSkillAction,
    InvokeSkillObservation,
    InvokeSkillTool,
)
from openhands.sdk.workspace.local import LocalWorkspace


def _make_skill(
    name: str,
    content: str = "# body\n\nSome guidance.",
    is_agentskills_format: bool = True,
    trigger=None,
) -> Skill:
    return Skill(
        name=name,
        content=content,
        description=f"desc for {name}",
        source=f"/skills/{name}/SKILL.md",
        is_agentskills_format=is_agentskills_format,
        trigger=trigger,
    )


def _make_conv(
    skills: list[Skill],
    working_dir: str = "/tmp",
    invoked_skills: list[str] | None = None,
) -> Any:
    """Minimal duck-typed BaseConversation replacement for the executor.

    Returned as `Any` so pyright accepts it where a `LocalConversation`
    is declared; the executor only uses attribute access, so a
    SimpleNamespace is enough at runtime.
    """
    return SimpleNamespace(
        agent=SimpleNamespace(
            agent_context=SimpleNamespace(skills=skills),
        ),
        workspace=SimpleNamespace(working_dir=working_dir),
        _state=SimpleNamespace(invoked_skills=invoked_skills or []),
    )


def _tool() -> InvokeSkillTool:
    (t,) = InvokeSkillTool.create()
    return t


def _run(name: str, conv: Any) -> InvokeSkillObservation:
    """Invoke the executor, silencing pyright's Optional complaint on .executor."""
    executor = _tool().executor
    assert executor is not None
    return executor(InvokeSkillAction(name=name), conversation=conv)


def test_not_in_default_builtins_but_resolvable_by_name():
    # Deliberately NOT in BUILT_IN_TOOLS: it must only attach when an
    # AgentSkills-format skill is loaded.
    assert InvokeSkillTool not in BUILT_IN_TOOLS
    # Still resolvable by name so the agent can wire it up conditionally.
    assert BUILT_IN_TOOL_CLASSES["InvokeSkillTool"] is InvokeSkillTool


def test_name_auto_derived():
    assert InvokeSkillTool.name == "invoke_skill"


def test_create_rejects_params():
    with pytest.raises(ValueError):
        InvokeSkillTool.create(foo="bar")


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("readOnlyHint", True),
        ("destructiveHint", False),
        ("idempotentHint", True),
        ("openWorldHint", False),
    ],
)
def test_annotations_are_read_only_safe(attr: str, expected: bool):
    t = _tool()
    assert t.annotations is not None
    assert getattr(t.annotations, attr) is expected


@pytest.mark.parametrize(
    ("content", "present", "absent"),
    [
        pytest.param(
            "Rule 1.\nRule 2.",
            "Rule 1.",
            None,
            id="static-content",
        ),
        pytest.param(
            "before !`echo TOKEN_OK` after",
            "TOKEN_OK",
            "!`echo",
            id="dynamic-shell-token-executed",
        ),
    ],
)
def test_invoke_renders_and_records(
    content: str, present: str, absent: str | None, tmp_path
):
    skill = _make_skill("s", content=content)
    conv = _make_conv([skill], working_dir=str(tmp_path))

    obs = _run("s", conv)

    assert obs.is_error is False
    assert obs.skill_name == "s"
    assert present in obs.text
    if absent is not None:
        assert absent not in obs.text
    assert conv._state.invoked_skills == ["s"]


@pytest.mark.parametrize(
    "requested",
    ["pdf-analyst", "  pdf-analyst  ", "\tpdf-analyst\n"],
    ids=["exact", "padded-spaces", "padded-tabs-newlines"],
)
def test_name_is_trimmed_before_lookup(requested: str):
    conv = _make_conv([_make_skill("pdf-analyst")])

    obs = _run(requested, conv)

    assert obs.is_error is False
    assert obs.skill_name == "pdf-analyst"


def test_invoked_skills_dedupes():
    conv = _make_conv([_make_skill("x")])

    _run("x", conv)
    _run("x", conv)

    assert conv._state.invoked_skills == ["x"]


def test_legacy_triggered_skill_is_invocable():
    """Any Skill in agent_context.skills is resolvable, not just
    AgentSkills-format. This keeps the executor consistent with what the
    `<available_skills>` prompt block advertises."""
    legacy = _make_skill(
        "flarglebargle",
        content="legacy body",
        is_agentskills_format=False,
        trigger=KeywordTrigger(keywords=["flarglebargle"]),
    )
    conv = _make_conv([legacy])

    obs = _run("flarglebargle", conv)

    assert obs.is_error is False
    assert "legacy body" in obs.text
    assert conv._state.invoked_skills == ["flarglebargle"]


@pytest.mark.parametrize(
    ("conv_factory", "requested", "expected_substrings"),
    [
        pytest.param(
            lambda: _make_conv([_make_skill("alpha"), _make_skill("beta")]),
            "gamma",
            ("Unknown skill 'gamma'", "alpha", "beta"),
            id="name-not-in-catalog",
        ),
        pytest.param(
            lambda: _make_conv([]),
            "anything",
            ("Unknown skill 'anything'", "<none>"),
            id="empty-catalog",
        ),
        pytest.param(
            lambda: None,
            "anything",
            ("Unknown skill 'anything'", "<none>"),
            id="no-conversation",
        ),
    ],
)
def test_error_paths_do_not_mutate_state(
    conv_factory, requested: str, expected_substrings: tuple[str, ...]
):
    conv = conv_factory()

    obs = _run(requested, conv)

    assert obs.is_error is True
    assert obs.skill_name == requested
    for expected in expected_substrings:
        assert expected in obs.text
    if conv is not None:
        assert conv._state.invoked_skills == []


@pytest.mark.parametrize(
    "skill_name",
    ["pdf-analyst", "frontend-design", "with space"],
)
def test_declared_resources_keyed_on_skill_name(skill_name: str):
    res = _tool().declared_resources(InvokeSkillAction(name=skill_name))

    assert res.declared is True
    assert res.keys == (f"skill:{skill_name.strip()}",)


def _make_agent(skills: list[Skill]) -> Agent:
    llm = LLM(
        usage_id="agent",
        model="anthropic/claude-sonnet-4-5-20250929",
        api_key=SecretStr("x"),
    )
    return Agent(llm=llm, tools=[], agent_context=AgentContext(skills=skills))


@pytest.mark.parametrize(
    ("skills", "expect_attached"),
    [
        pytest.param([], False, id="no-skills"),
        pytest.param(
            [_make_skill("legacy", is_agentskills_format=False)],
            False,
            id="only-legacy-skill",
        ),
        pytest.param(
            [_make_skill("frontend-design", is_agentskills_format=True)],
            True,
            id="agentskills-present",
        ),
    ],
)
def test_agent_auto_attaches_invoke_skill_tool(
    skills: list[Skill], expect_attached: bool, tmp_path
):
    """`Agent._initialize` must attach `invoke_skill` iff an AgentSkills-format
    skill is loaded — regardless of what's in `include_default_tools`."""
    agent = _make_agent(skills)
    state = ConversationState.create(
        id=uuid.uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
    )
    agent._initialize(state)

    attached = "invoke_skill" in agent._tools
    assert attached is expect_attached
