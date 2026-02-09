"""Agent-specific runtime state constants.

This module previously contained AgentStateRegistry, which has been replaced
by a simple dict[str, Any] field (agent_state) on ConversationState.

The ITERATIVE_REFINEMENT_ITERATION_KEY constant is kept here for backward
compatibility but is also defined in critic_mixin.py where it's primarily used.
"""

from openhands.sdk.agent.critic_mixin import ITERATIVE_REFINEMENT_ITERATION_KEY


__all__ = ["ITERATIVE_REFINEMENT_ITERATION_KEY"]
