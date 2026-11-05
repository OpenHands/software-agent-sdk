"""Trusted startup catalog for agent classes selected by settings."""

from __future__ import annotations

import inspect
import sys
from threading import RLock

from openhands.sdk.agent.agent import Agent
from openhands.sdk.agent.base import AgentBase


_registered_agents: dict[str, type[AgentBase]] = {}
_registry_lock = RLock()
_sealed = False


def _validate_agent_class(agent_class: type[AgentBase]) -> None:
    if not isinstance(agent_class, type) or not issubclass(agent_class, AgentBase):
        raise TypeError("Registered agents must be AgentBase classes.")
    if agent_class.__name__ == "CodeActAgent":
        raise ValueError("CodeActAgent is reserved for the stock Agent.")
    if inspect.isabstract(agent_class):
        raise ValueError("Registered agents must be concrete classes.")

    module = sys.modules.get(agent_class.__module__)
    if (
        agent_class.__module__ == "__main__"
        or agent_class.__qualname__ != agent_class.__name__
        or module is None
        or vars(module).get(agent_class.__name__) is not agent_class
    ):
        raise ValueError("Registered agents must be available module-level classes.")

    if AgentBase.resolve_kind(agent_class.__name__) is not agent_class:
        raise ValueError(
            "Registered agents must have a unique reconstruction identity."
        )


def register_agent(agent_class: type[AgentBase]) -> None:
    """Approve a concrete agent class during trusted application startup.

    The class name becomes its exact settings selector. Repeating the same
    registration before sealing is harmless; entries cannot be replaced.
    """
    with _registry_lock:
        if _sealed:
            raise ValueError("The agent registry is sealed.")
        _validate_agent_class(agent_class)
        name = agent_class.__name__
        existing = _registered_agents.get(name)
        if existing is not None and existing is not agent_class:
            raise ValueError("An agent with this name is already registered.")
        _registered_agents[name] = agent_class


def seal_agent_registry() -> None:
    """Prevent further registration before accepting untrusted configuration."""
    global _sealed
    with _registry_lock:
        _sealed = True


def _get_agent_class(selector: str) -> type[AgentBase]:
    """Seal the catalog and resolve one exact approved settings selector."""
    with _registry_lock:
        seal_agent_registry()
        if selector == "CodeActAgent":
            return Agent
        agent_class = _registered_agents.get(selector)
        if agent_class is None:
            raise ValueError(
                "Agent selector is not registered for profile construction."
            )
        # Later imports can change the SDK's kind hierarchy without registering.
        _validate_agent_class(agent_class)
        return agent_class
