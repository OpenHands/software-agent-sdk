"""Public vs marketplace skill precedence in the effective conversation context."""

from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.marketplace import MarketplaceRegistration
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.skills import Skill
from openhands.sdk.skills.skill import PUBLIC_SKILLS_ENABLED_ENV, load_public_skills
from tests.public_extension_fixtures import (
    ORG_GITHUB,
    PLUGIN_GITHUB,
    PUBLIC_GITHUB,
    make_plugin_marketplace,
    make_standalone_marketplace,
    public_repo,  # noqa: F401
)


pytestmark = pytest.mark.usefixtures("public_repo")


class _NoMCP:
    def __init__(self):
        self.created: list[dict] = []

    def create_tools(self, mcp_config, timeout=30.0, **_: Any) -> MCPClient:
        self.created.append(mcp_config)
        client: Any = type("C", (), {"tools": []})()
        return cast(MCPClient, client)


def _conversation(
    tmp_path: Path, registration: MarketplaceRegistration, **context: Any
) -> tuple[LocalConversation, _NoMCP]:
    context.setdefault("skills", load_public_skills())
    agent = Agent(
        llm=LLM(model="test/model", api_key=SecretStr("k")),
        tools=[],
        agent_context=AgentContext(registered_marketplaces=[registration], **context),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    mcp = _NoMCP()
    conversation = LocalConversation(
        agent=agent, workspace=workspace, visualizer=None, mcp_tool_provider=mcp
    )
    return conversation, mcp


def _skills(conversation: LocalConversation) -> dict[str, str]:
    context = conversation.agent.agent_context
    assert context is not None
    return {skill.name: skill.content.strip() for skill in context.skills}


def _registration(path: Path, precedence=None, auto_load=True):
    return MarketplaceRegistration(
        name="org",
        source=str(path),
        auto_load=auto_load,
        public_skill_precedence=precedence,
    )


@pytest.mark.parametrize(
    ("precedence", "expected"),
    [(None, PLUGIN_GITHUB), ("public", PUBLIC_GITHUB), ("marketplace", PLUGIN_GITHUB)],
)
def test_plugin_skill_precedence_keeps_other_plugin_components(
    tmp_path, precedence, expected
):
    org = make_plugin_marketplace(tmp_path / "org")
    conversation, mcp = _conversation(tmp_path, _registration(org, precedence))
    with patch(
        "openhands.sdk.conversation.impl.local_conversation.register_plugin_agents"
    ) as register:
        conversation._ensure_agent_ready()

    skills = _skills(conversation)
    assert skills["github"] == expected
    assert sum("GITHUB-MARKER" in body for body in skills.values()) == 1
    assert "plugin-other" in skills and "public-only" in skills
    assert "org-server" in conversation.agent.mcp_config
    assert any("org-server" in config for config in mcp.created)
    hook_config = conversation.state.hook_config
    assert hook_config is not None
    assert [m.matcher for m in hook_config.pre_tool_use] == ["org-*"]
    agents = register.call_args.kwargs["agents"]
    assert [agent.name for agent in agents] == ["org-helper"]
    conversation.close()


@pytest.mark.parametrize(
    ("precedence", "expected"),
    [(None, PUBLIC_GITHUB), ("public", PUBLIC_GITHUB), ("marketplace", ORG_GITHUB)],
)
def test_standalone_skill_precedence(tmp_path, precedence, expected):
    org = make_standalone_marketplace(tmp_path / "org")
    conversation, _ = _conversation(tmp_path, _registration(org, precedence))
    conversation._ensure_plugins_loaded()

    skills = _skills(conversation)
    assert skills["github"] == expected
    assert "org-unique" in skills and "public-only" in skills
    conversation.close()


@pytest.mark.parametrize("precedence", [None, "public", "marketplace"])
@pytest.mark.parametrize(
    ("factory", "org_marker"),
    [
        (make_standalone_marketplace, ORG_GITHUB),
        (make_plugin_marketplace, PLUGIN_GITHUB),
    ],
)
def test_public_disabled_organization_wins_by_availability(
    tmp_path, monkeypatch, precedence, factory, org_marker
):
    monkeypatch.setenv(PUBLIC_SKILLS_ENABLED_ENV, "false")
    org = factory(tmp_path / "org")
    conversation, _ = _conversation(
        tmp_path,
        _registration(org, precedence),
        skills=[],
        load_public_skills=True,  # caller-supplied setting cannot re-enable
    )
    conversation._ensure_plugins_loaded()

    skills = _skills(conversation)
    assert skills["github"] == org_marker
    assert "public-only" not in skills
    assert PUBLIC_GITHUB not in "".join(skills.values())
    conversation.close()


def test_preferred_marketplace_does_not_replace_non_public_skill(tmp_path):
    org = make_standalone_marketplace(tmp_path / "org")
    explicit = Skill(name="github", content="EXPLICIT-GITHUB", trigger=None)
    conversation, _ = _conversation(
        tmp_path, _registration(org, "marketplace"), skills=[explicit]
    )
    conversation._ensure_plugins_loaded()

    assert _skills(conversation)["github"] == "EXPLICIT-GITHUB"
    conversation.close()


@pytest.mark.parametrize(
    "factory", [make_standalone_marketplace, make_plugin_marketplace]
)
def test_auto_load_false_activates_nothing(tmp_path, factory):
    org = factory(tmp_path / "org")
    conversation, _ = _conversation(
        tmp_path, _registration(org, "marketplace", auto_load=False)
    )
    conversation._ensure_agent_ready()

    skills = _skills(conversation)
    assert skills["github"] == PUBLIC_GITHUB
    assert not {"org-unique", "plugin-other"} & skills.keys()
    assert "org-server" not in conversation.agent.mcp_config
    conversation.close()
