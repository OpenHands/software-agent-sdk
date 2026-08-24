"""Factory tools for OpenHands agents.

This package provides a ``factory_spawn`` tool that routes sub-work into the
factory as child conversations (parent-child linked, same workspace) or as new
roots (different workspace).

Usage:
    from openhands.tools.factory import FactorySpawnTool

    agent = Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name=FactorySpawnTool.name),
        ],
    )
"""

from openhands.tools.factory.definition import (
    FactorySpawnAction,
    FactorySpawnObservation,
    FactorySpawnTool,
)
from openhands.tools.factory.impl import FactorySpawnExecutor


__all__ = [
    "FactorySpawnAction",
    "FactorySpawnExecutor",
    "FactorySpawnObservation",
    "FactorySpawnTool",
]
