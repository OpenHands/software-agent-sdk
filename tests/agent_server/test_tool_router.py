"""Tests for tool_router module-level initialization."""

import importlib

import pytest

from openhands.sdk.subagent.registry import (
    _reset_registry_for_tests,
    get_agent_factory,
)


def test_builtin_agents_registered_on_tool_router_import():
    """Importing tool_router should register builtin agents (default, explore, bash).

    The agent-server includes tool_router at startup, so this verifies that
    builtin sub-agents are available as soon as the server starts.
    """
    import openhands.agent_server.tool_router as mod

    # Reset and reload to simulate a fresh import
    _reset_registry_for_tests()
    importlib.reload(mod)

    for name in ("default", "explore", "bash"):
        factory = get_agent_factory(name)
        assert factory is not None, f"Builtin agent '{name}' not registered"
        assert callable(factory.factory_func)

    _reset_registry_for_tests()


def test_browser_sub_agent_is_not_registered_when_the_browser_is_off(monkeypatch):
    import openhands.agent_server.config as config_mod
    import openhands.agent_server.tool_router as mod
    from openhands.agent_server.config import Config

    monkeypatch.setattr(config_mod, "_default_config", Config(enable_browser=False))
    _reset_registry_for_tests()
    try:
        importlib.reload(mod)
        assert get_agent_factory("explore") is not None
        with pytest.raises(ValueError):
            get_agent_factory("web-researcher")
    finally:
        _reset_registry_for_tests()
        monkeypatch.undo()
        importlib.reload(mod)


def test_catalog_offers_the_stock_tools_a_profile_may_pick():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openhands.agent_server.tool_router import tool_router

    app = FastAPI()
    app.include_router(tool_router, prefix="/api")
    response = TestClient(app).get("/api/tools/catalog")

    assert response.status_code == 200
    entries = {entry["name"]: entry for entry in response.json()["tools"]}
    selectable = {name for name, entry in entries.items() if entry["user_selectable"]}
    assert {
        "terminal",
        "file_editor",
        "task_tracker",
        "browser_tool_set",
        "glob",
        "grep",
        "workflow_tool_set",
        "ask_oracle",
        "task_tool_set",
        "switch_llm",
    } <= selectable
    assert "SwitchLLMTool" not in entries, (
        "built-ins are offered under their snake_case tool name"
    )
    assert entries["terminal"]["description"], "catalog carries a per-tool blurb"
    assert {name for name, entry in entries.items() if entry["in_default_set"]} == {
        "terminal",
        "file_editor",
        "task_tracker",
        "browser_tool_set",
        "switch_llm",
    }
    assert (
        not {
            "task",
            "workflow",
            "planning_file_editor",
            "edit",
            "read_file",
            "write_file",
            "list_directory",
        }
        & selectable
    )


def test_docker_runtime_catalog_does_not_report_host_usability(monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openhands.agent_server.tool_router import tool_router

    monkeypatch.setattr(
        "openhands.sdk.tool.registry._check_tool_usable", lambda name, checker: False
    )
    app = FastAPI()
    app.state.config = SimpleNamespace(
        conversation_runtime="docker",
        conversation_image_has_browser=True,
        enable_browser=True,
    )
    app.include_router(tool_router, prefix="/api")

    entries = TestClient(app).get("/api/tools/catalog").json()["tools"]

    assert entries and all(entry["usable"] for entry in entries)


def test_docker_catalog_follows_the_image_browser_setting(monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openhands.agent_server.tool_router import tool_router

    app = FastAPI()
    app.state.config = SimpleNamespace(
        conversation_runtime="docker",
        conversation_image_has_browser=False,
        enable_browser=True,
    )
    app.include_router(tool_router, prefix="/api")

    entries = TestClient(app).get("/api/tools/catalog").json()["tools"]
    usable = {entry["name"]: entry["usable"] for entry in entries}

    assert usable["browser_tool_set"] is False
    assert usable["terminal"] is True


def test_catalog_reports_a_disabled_browser_as_unusable():
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openhands.agent_server.tool_router import tool_router

    app = FastAPI()
    app.state.config = SimpleNamespace(
        conversation_runtime="local",
        conversation_image_has_browser=True,
        enable_browser=False,
    )
    app.include_router(tool_router, prefix="/api")

    entries = TestClient(app).get("/api/tools/catalog").json()["tools"]

    assert {e["name"]: e["usable"] for e in entries}["browser_tool_set"] is False
