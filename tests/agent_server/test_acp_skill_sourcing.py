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
from openhands.agent_server.conversation_service import _apply_acp_skill_sourcing
from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.agent import ACPAgent, AgentBase
from openhands.sdk.context import AgentContext
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
    assert _apply_acp_skill_sourcing(agent, "native") is agent


def test_native_sourcing_is_a_no_op_without_skills() -> None:
    agent = _acp_agent()
    assert _apply_acp_skill_sourcing(agent, "native") is agent
