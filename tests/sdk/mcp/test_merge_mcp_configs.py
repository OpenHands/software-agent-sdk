"""Tests for merge_mcp_configs."""

import pytest

from openhands.sdk.mcp.utils import merge_mcp_configs


def test_both_none():
    assert merge_mcp_configs(None, None) == {}


def test_base_none():
    override = {"mcpServers": {"s1": {"command": "node"}}}
    result = merge_mcp_configs(None, override)
    assert result == override
    assert result is not override  # new dict


def test_override_none():
    base = {"mcpServers": {"s1": {"command": "node"}}}
    result = merge_mcp_configs(base, None)
    assert result == base
    assert result is not base


def test_both_empty():
    assert merge_mcp_configs({}, {}) == {}


def test_disjoint_servers():
    base = {"mcpServers": {"s1": {"command": "base"}}}
    override = {"mcpServers": {"s2": {"command": "override"}}}
    result = merge_mcp_configs(base, override)
    assert result["mcpServers"] == {
        "s1": {"command": "base"},
        "s2": {"command": "override"},
    }


def test_override_wins_same_server():
    base = {"mcpServers": {"s1": {"command": "base", "args": ["-m", "old"]}}}
    override = {"mcpServers": {"s1": {"command": "override", "args": ["-m", "new"]}}}
    result = merge_mcp_configs(base, override)
    assert result["mcpServers"]["s1"]["command"] == "override"
    assert result["mcpServers"]["s1"]["args"] == ["-m", "new"]


def test_non_server_keys_override():
    base = {"mcpServers": {"s1": {"command": "base"}}, "timeout": 10}
    override = {"timeout": 30}
    result = merge_mcp_configs(base, override)
    assert result["timeout"] == 30
    assert result["mcpServers"] == {"s1": {"command": "base"}}


def test_non_server_keys_added():
    base = {"mcpServers": {"s1": {"command": "base"}}}
    override = {"timeout": 30}
    result = merge_mcp_configs(base, override)
    assert result["timeout"] == 30


def test_inputs_not_mutated():
    base = {"mcpServers": {"s1": {"command": "base"}}}
    override = {"mcpServers": {"s2": {"command": "new"}}}
    base_copy = {"mcpServers": {"s1": {"command": "base"}}}
    override_copy = {"mcpServers": {"s2": {"command": "new"}}}

    merge_mcp_configs(base, override)

    assert base == base_copy
    assert override == override_copy


def test_no_mcp_servers_key():
    """Merge works with plain top-level keys and no mcpServers."""
    base = {"customKey": "a"}
    override = {"customKey": "b", "other": 1}
    result = merge_mcp_configs(base, override)
    assert result == {"customKey": "b", "other": 1}


def test_override_adds_mcp_servers_to_base_without():
    base = {"timeout": 10}
    override = {"mcpServers": {"s1": {"command": "node"}}}
    result = merge_mcp_configs(base, override)
    assert result == {
        "timeout": 10,
        "mcpServers": {"s1": {"command": "node"}},
    }


@pytest.mark.parametrize(
    "base,override",
    [
        (None, {}),
        ({}, None),
        (None, {"k": "v"}),
        ({"k": "v"}, None),
    ],
)
def test_none_combinations(base, override):
    """Verify None handling doesn't crash and returns a dict."""
    result = merge_mcp_configs(base, override)
    assert isinstance(result, dict)
