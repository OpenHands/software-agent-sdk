"""Tests for tool_router module-level initialization."""

import importlib

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
