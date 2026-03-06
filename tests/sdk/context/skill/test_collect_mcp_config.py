"""Tests for collect_mcp_config."""

from openhands.sdk.context.skills import Skill, collect_mcp_config


def _skill(name: str, mcp_tools: dict | None = None) -> Skill:
    return Skill(name=name, content="placeholder", mcp_tools=mcp_tools)


def test_merges_servers_from_multiple_skills():
    skills = [
        _skill("a", {"mcpServers": {"s1": {"command": "cmd1"}}}),
        _skill("b", {"mcpServers": {"s2": {"command": "cmd2"}}}),
    ]
    result = collect_mcp_config(skills)
    assert result == {
        "mcpServers": {
            "s1": {"command": "cmd1"},
            "s2": {"command": "cmd2"},
        }
    }


def test_later_skills_override_by_server_name():
    skills = [
        _skill("a", {"mcpServers": {"s1": {"command": "old"}}}),
        _skill("b", {"mcpServers": {"s1": {"command": "new"}}}),
    ]
    result = collect_mcp_config(skills)
    assert result == {"mcpServers": {"s1": {"command": "new"}}}


def test_skips_skills_without_mcp_tools():
    skills = [
        _skill("no-mcp"),
        _skill("has-mcp", {"mcpServers": {"s1": {"command": "cmd1"}}}),
        _skill("empty-mcp", {"mcpServers": {}}),
    ]
    result = collect_mcp_config(skills)
    assert result == {"mcpServers": {"s1": {"command": "cmd1"}}}


def test_returns_empty_dict_when_no_skills_have_mcp():
    skills = [_skill("a"), _skill("b")]
    assert collect_mcp_config(skills) == {}


def test_returns_empty_dict_for_empty_list():
    assert collect_mcp_config([]) == {}
