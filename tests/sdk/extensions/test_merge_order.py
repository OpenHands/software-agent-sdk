"""Integration tests for the full Extensions merge precedence chain.

Production merge order (lowest → highest precedence):

    sandbox → public → user → org → project → plugins → inline

Skills and MCP config are last-wins: later sources override earlier.
Agents are first-wins: earlier sources are kept on collision.
Hooks concatenate: all sources' hooks run in merge order.
"""

from __future__ import annotations

from typing import Any

from openhands.sdk.extensions.extensions import Extensions
from openhands.sdk.hooks.config import HookConfig, HookDefinition, HookMatcher
from openhands.sdk.skills.skill import Skill
from openhands.sdk.subagent.schema import AgentDefinition


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _skill(name: str, content: str = "") -> Skill:
    return Skill(name=name, content=content or f"content:{name}")


def _agent(name: str, **kw: Any) -> AgentDefinition:
    return AgentDefinition(name=name, **kw)


def _hooks(command: str) -> HookConfig:
    return HookConfig(
        pre_tool_use=[
            HookMatcher(
                matcher="*",
                hooks=[HookDefinition(command=command)],
            )
        ]
    )


def _mcp(server_name: str, cmd: str) -> dict[str, Any]:
    return {"mcpServers": {server_name: {"command": cmd}}}


# ------------------------------------------------------------------
# Build realistic source bundles
# ------------------------------------------------------------------


def _build_chain() -> list[Extensions]:
    """Build the full production precedence chain.

    Order: sandbox, public, user, org, project, plugin, inline
    """
    sandbox = Extensions(
        skills=[_skill("work_hosts", "sandbox-hosts")],
    )

    public = Extensions(
        skills=[
            _skill("github", "public-github"),
            _skill("docker", "public-docker"),
            _skill("security", "public-security"),
        ],
    )

    user = Extensions(
        skills=[_skill("my-tool", "user-tool")],
        hooks=_hooks("echo user-hook"),
    )

    org = Extensions(
        skills=[
            _skill("org-policy", "org-policy-content"),
            _skill("security", "org-security-override"),
        ],
    )

    project = Extensions(
        skills=[
            _skill("github", "project-github-override"),
            _skill("project-guide", "project-guide-content"),
        ],
        hooks=_hooks("echo project-hook"),
        mcp_config=_mcp("fetch", "project-fetch"),
    )

    plugin = Extensions(
        skills=[_skill("lint-plugin", "plugin-lint")],
        hooks=_hooks("echo plugin-hook"),
        mcp_config=_mcp("fetch", "plugin-fetch-override"),
        agents=[_agent("reviewer", description="plugin-reviewer")],
    )

    inline = Extensions(
        skills=[_skill("inline-skill", "inline-content")],
        hooks=_hooks("echo inline-hook"),
        mcp_config=_mcp("inline-server", "inline-cmd"),
        agents=[_agent("reviewer", description="inline-reviewer")],
    )

    return [sandbox, public, user, org, project, plugin, inline]


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_full_chain_skill_count():
    """All unique skills from every source are present."""
    result = Extensions.collapse(_build_chain())
    names = {s.name for s in result.skills}
    expected = {
        "work_hosts",
        "github",
        "docker",
        "security",
        "my-tool",
        "org-policy",
        "project-guide",
        "lint-plugin",
        "inline-skill",
    }
    assert names == expected


def test_project_skill_overrides_public():
    """Project's 'github' skill overrides public's."""
    result = Extensions.collapse(_build_chain())
    github = next(s for s in result.skills if s.name == "github")
    assert github.content == "project-github-override"


def test_org_skill_overrides_public():
    """Org's 'security' skill overrides public's."""
    result = Extensions.collapse(_build_chain())
    security = next(s for s in result.skills if s.name == "security")
    assert security.content == "org-security-override"


def test_sandbox_skill_survives():
    """Sandbox's unique skill is preserved through the chain."""
    result = Extensions.collapse(_build_chain())
    hosts = next(s for s in result.skills if s.name == "work_hosts")
    assert hosts.content == "sandbox-hosts"


def test_plugin_mcp_overrides_project():
    """Plugin's 'fetch' MCP server overrides project's."""
    result = Extensions.collapse(_build_chain())
    assert result.mcp_config["mcpServers"]["fetch"]["command"] == (
        "plugin-fetch-override"
    )


def test_inline_mcp_adds_new_server():
    """Inline adds a new MCP server without clobbering existing ones."""
    result = Extensions.collapse(_build_chain())
    servers = result.mcp_config["mcpServers"]
    assert "fetch" in servers
    assert "inline-server" in servers


def test_hooks_concatenate_in_order():
    """All hooks from all sources are present in merge order."""
    result = Extensions.collapse(_build_chain())
    assert result.hooks is not None
    commands = [m.hooks[0].command for m in result.hooks.pre_tool_use]
    assert commands == [
        "echo user-hook",
        "echo project-hook",
        "echo plugin-hook",
        "echo inline-hook",
    ]


def test_agents_first_wins():
    """Plugin's 'reviewer' agent wins over inline's (first-wins)."""
    result = Extensions.collapse(_build_chain())
    assert len(result.agents) == 1
    assert result.agents[0].description == "plugin-reviewer"


def test_empty_sources_are_harmless():
    """Empty Extensions in the chain don't affect the result."""
    chain = [
        Extensions.empty(),
        Extensions(skills=[_skill("a", "first")]),
        Extensions.empty(),
        Extensions(skills=[_skill("b", "second")]),
        Extensions.empty(),
    ]
    result = Extensions.collapse(chain)
    assert {s.name for s in result.skills} == {"a", "b"}


def test_single_source_passthrough():
    """A chain with one source returns that source's content."""
    only = Extensions(
        skills=[_skill("s")],
        hooks=_hooks("echo only"),
        mcp_config=_mcp("srv", "cmd"),
        agents=[_agent("ag")],
    )
    result = Extensions.collapse([only])
    assert len(result.skills) == 1
    assert result.hooks is not None
    assert "srv" in result.mcp_config["mcpServers"]
    assert len(result.agents) == 1


def test_later_skill_overrides_earlier_across_many_sources():
    """The last source providing a name always wins for skills."""
    chain = [Extensions(skills=[_skill("x", f"source-{i}")]) for i in range(5)]
    result = Extensions.collapse(chain)
    assert len(result.skills) == 1
    assert result.skills[0].content == "source-4"


def test_first_agent_wins_across_many_sources():
    """The first source providing a name always wins for agents."""
    chain = [
        Extensions(agents=[_agent("x", description=f"source-{i}")]) for i in range(5)
    ]
    result = Extensions.collapse(chain)
    assert len(result.agents) == 1
    assert result.agents[0].description == "source-0"
