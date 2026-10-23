"""ACP skill sourcing: who supplies an ACP agent's skills (#4019).

An ACP CLI reads ``AGENTS.md`` / ``CLAUDE.md`` and its own project skills from
the session cwd, so OpenHands never injects those. Whether it injects its
*managed* catalog is a per-deployment choice (``Config.acp_skill_sourcing``):
a host-local CLI reaches the user's own configuration, one in a container
cannot.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from openhands.agent_server.config import ACPSkillSourcing, Config
from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.agent import ACPAgent, AgentBase
from openhands.sdk.context import AgentContext
from openhands.sdk.launch import LaunchRuntime, finalize
from openhands.sdk.marketplace.registration import MarketplaceRegistration
from openhands.sdk.settings.model import validate_agent_settings
from openhands.sdk.skills import Skill


AGENTS_MD_BODY = "sentinel-agents-md-body"
MANAGED_SKILL = "managed-catalog-skill"


def _managed_skill() -> Skill:
    return Skill(
        name=MANAGED_SKILL,
        content="managed content",
        description="from the server catalog",
        source="public",
        is_agentskills_format=True,
    )


def _acp_agent(**context_kwargs) -> ACPAgent:
    settings = validate_agent_settings(
        {
            "agent_kind": "acp",
            "acp_server": "claude-code",
            "agent_context": AgentContext(**context_kwargs).model_dump(),
        }
    )
    agent = settings.create_agent()
    assert isinstance(agent, ACPAgent)
    return agent


def _workspace(root: Path) -> Path:
    project = root / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text(f"# Repo\n\n{AGENTS_MD_BODY}\n")
    skill_dir = project / ".agents" / "skills" / "project-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: project-skill\ndescription: local\n---\n\nbody\n"
    )
    return project


def _apply_acp_skill_sourcing(
    agent: AgentBase, sourcing: ACPSkillSourcing
) -> AgentBase:
    return finalize(agent, LaunchRuntime(acp_skill_sourcing=sourcing)).agent


def _installed_suffix(agent: AgentBase, project: Path) -> str:
    """The suffix ``init_state`` renders into the ACP subprocess's prompt.

    Only the subprocess spawn is stubbed; the render is the production one.
    """
    with patch.object(ACPAgent, "_start_acp_server", lambda self, state: None):
        conversation = Conversation(agent=agent, workspace=str(project))
        conversation._ensure_agent_ready()
        agent_after_load = conversation.agent
        assert isinstance(agent_after_load, ACPAgent)
        return agent_after_load._installed_suffix or ""


def test_config_defaults_to_native_sourcing() -> None:
    assert Config().acp_skill_sourcing == "native"


def test_acp_agent_clears_load_project_skills() -> None:
    agent = _acp_agent(load_project_skills=True)
    assert agent.agent_context is not None
    assert agent.agent_context.load_project_skills is False


def test_acp_agent_clears_load_compatible_skills() -> None:
    """ACP CLIs read their own vendor skill dirs, so don't load them twice."""
    agent = _acp_agent(load_compatible_skills=True)
    assert agent.agent_context is not None
    assert agent.agent_context.load_compatible_skills is False


def test_acp_agent_keeps_vendor_user_skills_for_the_runtime_to_decide(
    tmp_path: Path, monkeypatch
) -> None:
    """Vendor user skills survive construction; ``finalize`` decides per runtime.

    ``AgentContext`` resolves ``load_compatible_skills`` into ``skills`` during
    validation. The runtime is unknown at construction time, so the validator
    must not drop them — a container CLI cannot reach the host's vendor
    directories and needs them injected (``openhands_managed`` sourcing).
    """
    from openhands.sdk.skills import skill as skill_module

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(skill_module, "USER_SKILLS_DIRS", [home / ".agents" / "skills"])
    vendor_dir = home / ".claude" / "skills" / "vendor-skill"
    vendor_dir.mkdir(parents=True)
    (vendor_dir / "SKILL.md").write_text(
        "---\nname: vendor-skill\ndescription: d\n---\nbody\n"
    )

    agent = _acp_agent(load_compatible_skills=True)
    context = agent.agent_context
    assert context is not None
    assert context.load_compatible_skills is False
    assert [s.name for s in context.skills] == ["vendor-skill"]


def test_acp_agent_keeps_explicit_skill_sourced_from_a_vendor_dir(
    tmp_path: Path,
) -> None:
    """An explicit skill whose ``source`` happens to sit under a vendor dir is
    the caller's choice and must not be dropped by the ACP validator."""
    explicit = Skill(
        name="review",
        content="review content",
        description="explicit",
        source=str(tmp_path / ".claude" / "skills" / "review" / "SKILL.md"),
    )
    agent = _acp_agent(skills=[explicit])
    context = agent.agent_context
    assert context is not None
    assert [s.name for s in context.skills] == ["review"]


def test_openhands_agent_keeps_load_compatible_skills() -> None:
    agent = Agent(
        llm=LLM(model="gpt-4o", usage_id="agent"),
        tools=[],
        agent_context=AgentContext(load_compatible_skills=True),
    )
    assert agent.agent_context is not None
    assert agent.agent_context.load_compatible_skills is True


def test_openhands_agent_keeps_load_project_skills() -> None:
    """The guard is ACP-only — a regular agent still loads project skills."""
    agent = Agent(
        llm=LLM(model="gpt-4o", usage_id="agent"),
        tools=[],
        agent_context=AgentContext(load_project_skills=True),
    )
    assert agent.agent_context is not None
    assert agent.agent_context.load_project_skills is True


@pytest.mark.parametrize("sourcing", ["native", "openhands_managed"])
def test_repo_context_never_reaches_the_acp_prompt(
    tmp_path: Path, sourcing: ACPSkillSourcing
) -> None:
    project = _workspace(tmp_path)
    agent = _apply_acp_skill_sourcing(
        _acp_agent(skills=[_managed_skill()], load_project_skills=True), sourcing
    )
    suffix = _installed_suffix(agent, project)
    assert AGENTS_MD_BODY not in suffix
    assert "project-skill" not in suffix


def test_native_sourcing_strips_managed_skills(tmp_path: Path) -> None:
    project = _workspace(tmp_path)
    agent = _apply_acp_skill_sourcing(
        _acp_agent(skills=[_managed_skill()], load_project_skills=True), "native"
    )
    assert agent.agent_context is not None
    assert agent.agent_context.skills == []
    assert MANAGED_SKILL not in _installed_suffix(agent, project)


def test_managed_sourcing_keeps_managed_skills(tmp_path: Path) -> None:
    project = _workspace(tmp_path)
    agent = _apply_acp_skill_sourcing(
        _acp_agent(skills=[_managed_skill()], load_project_skills=True),
        "openhands_managed",
    )
    assert agent.agent_context is not None
    assert [s.name for s in agent.agent_context.skills] == [MANAGED_SKILL]
    assert MANAGED_SKILL in _installed_suffix(agent, project)


def test_managed_sourcing_keeps_vendor_user_skills(tmp_path: Path, monkeypatch) -> None:
    """A container CLI cannot reach the host's vendor dirs, so managed sourcing
    must inject the vendor user skills it eagerly resolved (Finding 1)."""
    from openhands.sdk.skills import skill as skill_module

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(skill_module, "USER_SKILLS_DIRS", [home / ".agents" / "skills"])
    vendor_dir = home / ".claude" / "skills" / "review"
    vendor_dir.mkdir(parents=True)
    (vendor_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: d\n---\nhost vendor body\n"
    )
    project = _workspace(tmp_path)

    agent = _apply_acp_skill_sourcing(
        _acp_agent(load_compatible_skills=True), "openhands_managed"
    )

    context = agent.agent_context
    assert context is not None
    assert "review" in {s.name for s in context.skills}
    assert "review" in _installed_suffix(agent, project)


def test_native_sourcing_drops_vendor_user_skills(tmp_path: Path, monkeypatch) -> None:
    """A host-local CLI reads its own vendor dirs, so native sourcing strips
    them to avoid duplicating the catalog in the prompt."""
    from openhands.sdk.skills import skill as skill_module

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(skill_module, "USER_SKILLS_DIRS", [home / ".agents" / "skills"])
    vendor_dir = home / ".claude" / "skills" / "review"
    vendor_dir.mkdir(parents=True)
    (vendor_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: d\n---\nhost vendor body\n"
    )
    project = _workspace(tmp_path)

    agent = _apply_acp_skill_sourcing(_acp_agent(load_compatible_skills=True), "native")

    assert agent.agent_context is not None
    assert agent.agent_context.skills == []
    assert "review" not in _installed_suffix(agent, project)


def _vendor_skill_in_home(
    tmp_path: Path,
    monkeypatch,
    name: str = "review",
    vendor: str = ".claude",
) -> None:
    from openhands.sdk.skills import skill as skill_module

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(skill_module, "USER_SKILLS_DIRS", [home / ".agents" / "skills"])
    vendor_dir = home / vendor / "skills" / name
    vendor_dir.mkdir(parents=True)
    (vendor_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\nhost vendor body\n"
    )


def test_direct_acp_conversation_does_not_inject_vendor_skills(
    tmp_path: Path, monkeypatch
) -> None:
    """A directly-built ACP agent (no ``finalize``) keeps the native default.

    Nothing in the direct path knows the runtime, so the agent must not render
    the vendor user skills its native CLI already reads — otherwise the catalog
    appears twice in the prompt (round-4 finding). The construction still keeps
    ``skills`` so a managed runtime can recover them.
    """
    _vendor_skill_in_home(tmp_path, monkeypatch)
    project = _workspace(tmp_path)

    agent = _acp_agent(load_compatible_skills=True)

    context = agent.agent_context
    assert context is not None
    assert [s.name for s in context.skills] == ["review"]
    assert agent.acp_skill_sourcing is None
    assert "review" not in _installed_suffix(agent, project)


def test_direct_acp_conversation_injects_skills_when_marked_managed(
    tmp_path: Path, monkeypatch
) -> None:
    """An agent explicitly marked ``openhands_managed`` renders its catalog."""
    _vendor_skill_in_home(tmp_path, monkeypatch)
    project = _workspace(tmp_path)

    agent = _acp_agent(load_compatible_skills=True).model_copy(
        update={"acp_skill_sourcing": "openhands_managed"}
    )

    assert "review" in _installed_suffix(agent, project)


def test_finalize_records_the_runtime_sourcing_on_the_agent() -> None:
    """``finalize`` stamps the runtime choice so the render can honour it."""
    agent = _apply_acp_skill_sourcing(_acp_agent(current_datetime=None), "native")
    assert isinstance(agent, ACPAgent)
    assert agent.acp_skill_sourcing == "native"

    managed = _apply_acp_skill_sourcing(
        _acp_agent(current_datetime=None), "openhands_managed"
    )
    assert isinstance(managed, ACPAgent)
    assert managed.acp_skill_sourcing == "openhands_managed"


def test_native_sourcing_clears_lazy_skill_sources() -> None:
    """Flags and marketplace registrations resolve to skills later, so a strip
    that only emptied ``skills`` would let them back in."""
    agent = _apply_acp_skill_sourcing(
        _acp_agent(
            load_user_skills=True,
            load_public_skills=True,
            registered_marketplaces=[
                MarketplaceRegistration(
                    name="mkt", source="https://example.invalid/mkt.git"
                )
            ],
        ),
        "native",
    )
    context = agent.agent_context
    assert context is not None
    assert context.load_user_skills is False
    assert context.load_public_skills is False
    assert context.registered_marketplaces == []


def test_native_sourcing_leaves_a_non_acp_agent_alone() -> None:
    agent = Agent(
        llm=LLM(model="gpt-4o", usage_id="agent"),
        tools=[],
        agent_context=AgentContext(skills=[_managed_skill()]),
    )
    sourced = _apply_acp_skill_sourcing(agent, "native")
    assert sourced.agent_context is not None
    assert [s.name for s in sourced.agent_context.skills] == [MANAGED_SKILL]


def test_native_sourcing_is_a_no_op_without_skills() -> None:
    agent = _acp_agent(current_datetime=None).model_copy(
        update={"acp_skill_sourcing": "native"}
    )
    assert _apply_acp_skill_sourcing(agent, "native") is agent


def _explicit_skill(name: str) -> Skill:
    return Skill(
        name=name,
        content=f"{name} content",
        description=f"{name} description",
    )


def test_direct_acp_conversation_renders_explicit_skills(tmp_path: Path) -> None:
    """A directly-built agent keeps explicit skills with compatible loading off.

    Regression: the old default ("native") stripped every managed skill at
    render, so an explicitly supplied skill silently vanished from a direct
    ``Conversation(agent=ACPAgent(...))`` that never ran through ``finalize``.
    """
    project = _workspace(tmp_path)
    agent = _acp_agent(skills=[_explicit_skill("my-skill")])
    assert agent.acp_skill_sourcing is None
    assert "my-skill" in _installed_suffix(agent, project)


def test_direct_acp_conversation_drops_only_compatible_skills(
    tmp_path: Path, monkeypatch
) -> None:
    """With compatible loading on, only vendor-loaded skills leave the prompt."""
    _vendor_skill_in_home(tmp_path, monkeypatch, name="review")
    project = _workspace(tmp_path)

    agent = _acp_agent(
        skills=[_explicit_skill("my-skill")], load_compatible_skills=True
    )

    assert "review" not in _installed_suffix(agent, project)
    assert "my-skill" in _installed_suffix(agent, project)


def test_direct_acp_drops_inherited_skills_from_other_vendors(
    tmp_path: Path, monkeypatch
) -> None:
    """A directly-built ACP agent advertises no auto-loaded compatible skill.

    An ACP agent runs no OpenHands tools, so the rendered ``<SKILLS>`` catalog
    has no invocation path: the prompt names ``invoke_skill`` (which the agent
    does not expose) and omits each skill's location. A skill loaded from
    another vendor's directory than the selected CLI's — here ``.codex`` under a
    Claude CLI — is therefore unreachable, so it must not be advertised.
    """
    _vendor_skill_in_home(tmp_path, monkeypatch, name="claude-skill", vendor=".claude")
    _vendor_skill_in_home(tmp_path, monkeypatch, name="codex-skill", vendor=".codex")
    project = _workspace(tmp_path)

    suffix = _installed_suffix(_acp_agent(load_compatible_skills=True), project)

    assert "claude-skill" not in suffix
    assert "codex-skill" not in suffix


def test_direct_acp_drops_inherited_skills_regardless_of_provider(
    tmp_path: Path, monkeypatch
) -> None:
    """The suppression is provider-independent — a CLI for another vendor also
    cannot invoke a catalog entry, so its inherited skills leave too."""
    _vendor_skill_in_home(tmp_path, monkeypatch, name="claude-skill", vendor=".claude")
    _vendor_skill_in_home(tmp_path, monkeypatch, name="codex-skill", vendor=".codex")
    project = _workspace(tmp_path)

    settings = validate_agent_settings(
        {
            "agent_kind": "acp",
            "acp_server": "codex",
            "agent_context": AgentContext(load_compatible_skills=True).model_dump(),
        }
    )
    agent = settings.create_agent()
    assert isinstance(agent, ACPAgent)

    suffix = _installed_suffix(agent, project)

    assert "codex-skill" not in suffix
    assert "claude-skill" not in suffix


def test_direct_acp_keeps_explicit_skill_sourced_from_vendor_dir(
    tmp_path: Path, monkeypatch
) -> None:
    """An explicit skill whose ``source`` sits under a vendor dir is the caller's
    choice and must not be suppressed — only auto-loaded compatible skills are.

    Regression for the old path-based filter, which dropped any skill whose
    ``source`` happened to lie under a vendor directory even when compatible
    loading was off.
    """
    _vendor_skill_in_home(tmp_path, monkeypatch)
    project = _workspace(tmp_path)
    home = tmp_path / "home"

    explicit = Skill(
        name="explicit-review",
        content="explicit content",
        description="explicit",
        source=str(home / ".claude" / "skills" / "review" / "SKILL.md"),
    )
    agent = _acp_agent(skills=[explicit])

    assert "explicit-review" in _installed_suffix(agent, project)
