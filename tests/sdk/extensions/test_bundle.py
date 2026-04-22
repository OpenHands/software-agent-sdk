"""Tests for Extensions model and merge semantics."""

from __future__ import annotations

from typing import Any

import pytest

from openhands.sdk.extensions.extensions import Extensions
from openhands.sdk.hooks.config import HookConfig, HookDefinition, HookMatcher
from openhands.sdk.skills.skill import Skill
from openhands.sdk.subagent.schema import AgentDefinition


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _skill(name: str, content: str = "") -> Skill:
    return Skill(name=name, content=content or f"content for {name}")


def _agent(name: str, **kwargs: Any) -> AgentDefinition:
    return AgentDefinition(name=name, **kwargs)


def _hook_config(**matchers: list[HookMatcher]) -> HookConfig:
    return HookConfig(**matchers)


def _hook_matcher(matcher: str = "*", command: str = "echo ok") -> HookMatcher:
    return HookMatcher(matcher=matcher, hooks=[HookDefinition(command=command)])


# ------------------------------------------------------------------
# empty / is_empty
# ------------------------------------------------------------------


def test_empty_returns_empty_bundle():
    ext = Extensions.empty()
    assert ext.skills == []
    assert ext.hooks is None
    assert ext.mcp_config == {}
    assert ext.agents == []


def test_is_empty_on_default():
    assert Extensions.empty().is_empty()


def test_is_empty_false_with_skills():
    ext = Extensions(skills=[_skill("a")])
    assert not ext.is_empty()


def test_is_empty_false_with_hooks():
    ext = Extensions(hooks=HookConfig())
    assert not ext.is_empty()


def test_is_empty_false_with_mcp():
    ext = Extensions(mcp_config={"mcpServers": {"s": {}}})
    assert not ext.is_empty()


def test_is_empty_false_with_agents():
    ext = Extensions(agents=[_agent("a")])
    assert not ext.is_empty()


# ------------------------------------------------------------------
# Immutability
# ------------------------------------------------------------------


def test_frozen():
    ext = Extensions.empty()
    with pytest.raises(Exception):
        ext.skills = [_skill("x")]  # type: ignore[misc]


# ------------------------------------------------------------------
# Skills merge — last-wins
# ------------------------------------------------------------------


def test_skills_merge_no_overlap():
    a = Extensions(skills=[_skill("x")])
    b = Extensions(skills=[_skill("y")])
    merged = a.merge(b)
    assert {s.name for s in merged.skills} == {"x", "y"}


def test_skills_merge_override():
    a = Extensions(skills=[_skill("x", "old")])
    b = Extensions(skills=[_skill("x", "new")])
    merged = a.merge(b)
    assert len(merged.skills) == 1
    assert merged.skills[0].content == "new"


def test_skills_merge_preserves_order():
    a = Extensions(skills=[_skill("a"), _skill("b")])
    b = Extensions(skills=[_skill("c")])
    merged = a.merge(b)
    assert [s.name for s in merged.skills] == ["a", "b", "c"]


def test_skills_override_preserves_position():
    """When other overrides a skill, it keeps the position of the original."""
    a = Extensions(skills=[_skill("a"), _skill("b"), _skill("c")])
    b = Extensions(skills=[_skill("b", "new")])
    merged = a.merge(b)
    assert [s.name for s in merged.skills] == ["a", "b", "c"]
    assert merged.skills[1].content == "new"


# ------------------------------------------------------------------
# Hooks merge — concatenate
# ------------------------------------------------------------------


def test_hooks_merge_both_none():
    merged = Extensions.empty().merge(Extensions.empty())
    assert merged.hooks is None


def test_hooks_merge_base_only():
    cfg = _hook_config(pre_tool_use=[_hook_matcher(command="echo base")])
    merged = Extensions(hooks=cfg).merge(Extensions.empty())
    assert merged.hooks is not None
    assert len(merged.hooks.pre_tool_use) == 1


def test_hooks_merge_override_only():
    cfg = _hook_config(pre_tool_use=[_hook_matcher(command="echo over")])
    merged = Extensions.empty().merge(Extensions(hooks=cfg))
    assert merged.hooks is not None
    assert len(merged.hooks.pre_tool_use) == 1


def test_hooks_merge_concatenation():
    base_cfg = _hook_config(pre_tool_use=[_hook_matcher(command="echo 1")])
    over_cfg = _hook_config(pre_tool_use=[_hook_matcher(command="echo 2")])
    merged = Extensions(hooks=base_cfg).merge(Extensions(hooks=over_cfg))
    assert merged.hooks is not None
    assert len(merged.hooks.pre_tool_use) == 2
    cmds = [m.hooks[0].command for m in merged.hooks.pre_tool_use]
    assert cmds == ["echo 1", "echo 2"]


# ------------------------------------------------------------------
# MCP config merge — last-wins
# ------------------------------------------------------------------


def test_mcp_merge_no_overlap():
    a = Extensions(mcp_config={"mcpServers": {"s1": {"cmd": "a"}}})
    b = Extensions(mcp_config={"mcpServers": {"s2": {"cmd": "b"}}})
    merged = a.merge(b)
    assert set(merged.mcp_config["mcpServers"]) == {"s1", "s2"}


def test_mcp_merge_server_override():
    a = Extensions(mcp_config={"mcpServers": {"s": {"cmd": "old"}}})
    b = Extensions(mcp_config={"mcpServers": {"s": {"cmd": "new"}}})
    merged = a.merge(b)
    assert merged.mcp_config["mcpServers"]["s"]["cmd"] == "new"


def test_mcp_merge_top_level_key_override():
    a = Extensions(mcp_config={"mcpServers": {}, "timeout": 10})
    b = Extensions(mcp_config={"timeout": 30})
    merged = a.merge(b)
    assert merged.mcp_config["timeout"] == 30
    assert merged.mcp_config["mcpServers"] == {}


def test_mcp_merge_both_empty():
    merged = Extensions.empty().merge(Extensions.empty())
    assert merged.mcp_config == {}


def test_mcp_merge_base_only():
    a = Extensions(mcp_config={"mcpServers": {"s": {}}})
    merged = a.merge(Extensions.empty())
    assert "s" in merged.mcp_config["mcpServers"]


def test_mcp_merge_does_not_mutate_inputs():
    base_cfg: dict[str, Any] = {"mcpServers": {"s1": {"cmd": "a"}}}
    over_cfg: dict[str, Any] = {"mcpServers": {"s1": {"cmd": "b"}}}
    a = Extensions(mcp_config=base_cfg)
    b = Extensions(mcp_config=over_cfg)
    a.merge(b)
    assert base_cfg["mcpServers"]["s1"]["cmd"] == "a"


# ------------------------------------------------------------------
# Agents merge — first-wins
# ------------------------------------------------------------------


def test_agents_merge_no_overlap():
    a = Extensions(agents=[_agent("x")])
    b = Extensions(agents=[_agent("y")])
    merged = a.merge(b)
    assert {ag.name for ag in merged.agents} == {"x", "y"}


def test_agents_merge_first_wins():
    a = Extensions(agents=[_agent("x", description="first")])
    b = Extensions(agents=[_agent("x", description="second")])
    merged = a.merge(b)
    assert len(merged.agents) == 1
    assert merged.agents[0].description == "first"


def test_agents_merge_preserves_order():
    a = Extensions(agents=[_agent("a"), _agent("b")])
    b = Extensions(agents=[_agent("c")])
    merged = a.merge(b)
    assert [ag.name for ag in merged.agents] == ["a", "b", "c"]


# ------------------------------------------------------------------
# collapse
# ------------------------------------------------------------------


def test_collapse_empty_list():
    assert Extensions.collapse([]).is_empty()


def test_collapse_single():
    ext = Extensions(skills=[_skill("a")])
    assert Extensions.collapse([ext]) == ext


def test_collapse_precedence():
    """Later entries override earlier for skills (last-wins)."""
    low = Extensions(skills=[_skill("s", "low")])
    high = Extensions(skills=[_skill("s", "high")])
    result = Extensions.collapse([low, high])
    assert result.skills[0].content == "high"


def test_collapse_agents_first_wins():
    """Earlier entries win for agents (first-wins)."""
    first = Extensions(agents=[_agent("a", description="first")])
    second = Extensions(agents=[_agent("a", description="second")])
    result = Extensions.collapse([first, second])
    assert result.agents[0].description == "first"


def test_collapse_hooks_accumulate():
    h1 = _hook_config(pre_tool_use=[_hook_matcher(command="echo 1")])
    h2 = _hook_config(pre_tool_use=[_hook_matcher(command="echo 2")])
    h3 = _hook_config(pre_tool_use=[_hook_matcher(command="echo 3")])
    result = Extensions.collapse(
        [Extensions(hooks=h1), Extensions(hooks=h2), Extensions(hooks=h3)]
    )
    assert result.hooks is not None
    assert len(result.hooks.pre_tool_use) == 3


def test_collapse_full_scenario():
    """Realistic multi-source collapse."""
    public = Extensions(
        skills=[_skill("github"), _skill("docker")],
        mcp_config={"mcpServers": {"fetch": {"cmd": "uvx mcp-server-fetch"}}},
    )
    user = Extensions(
        skills=[_skill("my-tool")],
        hooks=_hook_config(pre_tool_use=[_hook_matcher(command="lint.sh")]),
    )
    project = Extensions(
        skills=[_skill("github", "project-override")],
        agents=[_agent("reviewer")],
    )
    plugin = Extensions(
        skills=[_skill("security-scan")],
        hooks=_hook_config(pre_tool_use=[_hook_matcher(command="scan.sh")]),
        mcp_config={"mcpServers": {"fetch": {"cmd": "custom-fetch"}}},
        agents=[_agent("reviewer", description="plugin-reviewer")],
    )

    result = Extensions.collapse([public, user, project, plugin])

    # Skills: plugin's security-scan added, project's github override kept
    # (then overridden by plugin if plugin had one — it doesn't here)
    skill_names = {s.name for s in result.skills}
    assert skill_names == {"github", "docker", "my-tool", "security-scan"}
    github_skill = next(s for s in result.skills if s.name == "github")
    assert github_skill.content == "project-override"

    # MCP: plugin's fetch overrides public's
    assert result.mcp_config["mcpServers"]["fetch"]["cmd"] == "custom-fetch"

    # Hooks: user + plugin concatenated (2 matchers)
    assert result.hooks is not None
    assert len(result.hooks.pre_tool_use) == 2

    # Agents: project's reviewer wins (first-wins over plugin's)
    assert len(result.agents) == 1
    assert result.agents[0].description == ""  # project's default


# ------------------------------------------------------------------
# merge returns new instance (no mutation)
# ------------------------------------------------------------------


def test_merge_returns_new_instance():
    a = Extensions(skills=[_skill("a")])
    b = Extensions(skills=[_skill("b")])
    merged = a.merge(b)
    assert merged is not a
    assert merged is not b
    assert len(a.skills) == 1
    assert len(b.skills) == 1
