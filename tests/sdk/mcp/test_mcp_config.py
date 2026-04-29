"""Tests for openhands.sdk.mcp.config."""

import json
from pathlib import Path
from typing import Any

import pytest

from openhands.sdk.mcp.config import (
    expand_mcp_variables,
    find_mcp_config,
    load_mcp_config,
    merge_mcp_configs,
)


# -- find_mcp_config ----------------------------------------------------------


def test_find_mcp_config_present(tmp_path: Path):
    mcp_file = tmp_path / ".mcp.json"
    mcp_file.write_text("{}")
    assert find_mcp_config(tmp_path) == mcp_file


def test_find_mcp_config_absent(tmp_path: Path):
    assert find_mcp_config(tmp_path) is None


def test_find_mcp_config_not_a_dir(tmp_path: Path):
    f = tmp_path / "file.txt"
    f.write_text("")
    assert find_mcp_config(f) is None


# -- expand_mcp_variables -----------------------------------------------------


def test_expand_provided_variable():
    config: dict[str, Any] = {"mcpServers": {"s": {"env": {"ROOT": "${SKILL_ROOT}"}}}}
    result = expand_mcp_variables(config, {"SKILL_ROOT": "/skills/test"})
    assert result["mcpServers"]["s"]["env"]["ROOT"] == "/skills/test"


def test_expand_default_value():
    config: dict[str, Any] = {"mcpServers": {"s": {"env": {"PORT": "${PORT:-8080}"}}}}
    result = expand_mcp_variables(config, {})
    assert result["mcpServers"]["s"]["env"]["PORT"] == "8080"


def test_expand_defaults_false_preserves_placeholder():
    config: dict[str, Any] = {"mcpServers": {"s": {"env": {"PORT": "${PORT:-8080}"}}}}
    result = expand_mcp_variables(config, {}, expand_defaults=False)
    assert result["mcpServers"]["s"]["env"]["PORT"] == "${PORT:-8080}"


def test_expand_env_variable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MY_TEST_VAR", "env-value")
    config: dict[str, Any] = {"mcpServers": {"s": {"env": {"V": "${MY_TEST_VAR}"}}}}
    result = expand_mcp_variables(config, {})
    assert result["mcpServers"]["s"]["env"]["V"] == "env-value"


def test_expand_secret_callback():
    secrets = {"MY_SECRET": "secret-val"}
    config: dict[str, Any] = {"mcpServers": {"s": {"env": {"K": "${MY_SECRET}"}}}}
    result = expand_mcp_variables(config, {}, get_secret=secrets.get)
    assert result["mcpServers"]["s"]["env"]["K"] == "secret-val"


def test_expand_resolution_order(monkeypatch: pytest.MonkeyPatch):
    """Provided variables win over secrets and env."""
    monkeypatch.setenv("VAR", "from-env")
    secrets = {"VAR": "from-secret"}
    config: dict[str, Any] = {"mcpServers": {"s": {"v": "${VAR}"}}}
    result = expand_mcp_variables(config, {"VAR": "from-vars"}, get_secret=secrets.get)
    assert result["mcpServers"]["s"]["v"] == "from-vars"


def test_expand_pydantic_model_objects():
    """Pydantic model objects are serialized before expansion."""
    from pydantic import BaseModel

    class FakeServer(BaseModel):
        command: str
        env: dict[str, str]

    config = {"mcpServers": {"s": FakeServer(command="node", env={"K": "${V}"})}}
    result = expand_mcp_variables(config, {"V": "val"})
    assert result["mcpServers"]["s"]["command"] == "node"
    assert result["mcpServers"]["s"]["env"]["K"] == "val"


# -- load_mcp_config ----------------------------------------------------------


def test_load_mcp_config(tmp_path: Path):
    mcp_file = tmp_path / ".mcp.json"
    mcp_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "test": {
                        "command": "python",
                        "env": {"ROOT": "${SKILL_ROOT}"},
                    }
                }
            }
        )
    )
    result = load_mcp_config(mcp_file, root_dir=tmp_path)
    assert result["mcpServers"]["test"]["env"]["ROOT"] == str(tmp_path)


def test_load_mcp_config_invalid_json(tmp_path: Path):
    mcp_file = tmp_path / ".mcp.json"
    mcp_file.write_text("not json")
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_mcp_config(mcp_file)


def test_load_mcp_config_not_dict(tmp_path: Path):
    mcp_file = tmp_path / ".mcp.json"
    mcp_file.write_text("[]")
    with pytest.raises(ValueError, match="expected object"):
        load_mcp_config(mcp_file)


# -- merge_mcp_configs --------------------------------------------------------


def test_merge_both_none():
    assert merge_mcp_configs(None, None) == {}


def test_merge_base_none():
    override = {"mcpServers": {"s1": {"command": "node"}}}
    result = merge_mcp_configs(None, override)
    assert result == override
    assert result is not override


def test_merge_override_none():
    base = {"mcpServers": {"s1": {"command": "node"}}}
    result = merge_mcp_configs(base, None)
    assert result == base
    assert result is not base


def test_merge_disjoint_servers():
    base: dict[str, Any] = {"mcpServers": {"s1": {"command": "base"}}}
    override: dict[str, Any] = {"mcpServers": {"s2": {"command": "override"}}}
    result = merge_mcp_configs(base, override)
    assert result["mcpServers"] == {
        "s1": {"command": "base"},
        "s2": {"command": "override"},
    }


def test_merge_override_wins_same_server():
    base: dict[str, Any] = {
        "mcpServers": {"s1": {"command": "base", "args": ["-m", "old"]}}
    }
    override: dict[str, Any] = {
        "mcpServers": {"s1": {"command": "override", "args": ["-m", "new"]}}
    }
    result = merge_mcp_configs(base, override)
    assert result["mcpServers"]["s1"]["command"] == "override"


def test_merge_non_server_keys_override():
    base: dict[str, Any] = {
        "mcpServers": {"s1": {"command": "base"}},
        "timeout": 10,
    }
    override: dict[str, Any] = {"timeout": 30}
    result = merge_mcp_configs(base, override)
    assert result["timeout"] == 30
    assert result["mcpServers"] == {"s1": {"command": "base"}}


def test_merge_inputs_not_mutated():
    base: dict[str, Any] = {"mcpServers": {"s1": {"command": "base"}}}
    override: dict[str, Any] = {"mcpServers": {"s2": {"command": "new"}}}
    base_copy = {"mcpServers": {"s1": {"command": "base"}}}
    override_copy = {"mcpServers": {"s2": {"command": "new"}}}
    merge_mcp_configs(base, override)
    assert base == base_copy
    assert override == override_copy
