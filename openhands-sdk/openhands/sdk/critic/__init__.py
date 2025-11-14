from openhands.sdk.critic.base import BaseCritic, CriticResult
from openhands.sdk.critic.impl import (
    AgentFinishedCritic,
    EmptyPatchCritic,
    PassCritic,
)
from openhands.sdk.critic.registry import CriticRegistry


# Register default critics
CriticRegistry.register("finish_with_patch", AgentFinishedCritic)
CriticRegistry.register("empty_patch_critic", EmptyPatchCritic)
CriticRegistry.register("pass", PassCritic)


__all__ = [
    "BaseCritic",
    "CriticResult",
    "AgentFinishedCritic",
    "EmptyPatchCritic",
    "PassCritic",
    "CriticRegistry",
]
