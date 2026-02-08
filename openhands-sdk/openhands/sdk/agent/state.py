"""Agent-specific runtime state that persists across iterations."""

from typing import Any

from pydantic import Field

from openhands.sdk.utils.models import OpenHandsModel


class AgentState(OpenHandsModel):
    """Agent-specific runtime state that persists across conversation iterations.

    This class holds state that is specific to agent execution and behavior,
    separate from conversation-level state. It provides a centralized place
    for agent-specific features to store their runtime state.

    The AgentState is embedded within ConversationState and is automatically
    persisted along with the conversation.

    Example:
        # Access agent state from conversation state
        state.agent_state.iterative_refinement_iteration += 1

        # Store custom metadata
        state.agent_state.metadata["custom_key"] = "value"

    Attributes:
        iterative_refinement_iteration: Current iteration count for critic
            iterative refinement. Tracks how many refinement cycles have
            been performed.
        metadata: Extensible dictionary for agent-specific data that doesn't
            warrant a dedicated field. Use sparingly - prefer typed fields
            for frequently used state.
    """

    iterative_refinement_iteration: int = Field(
        default=0,
        ge=0,
        description="Current iteration count for critic iterative refinement. "
        "Tracks how many refinement cycles have been performed.",
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Extensible dictionary for agent-specific data. "
        "Use for custom state that doesn't warrant a dedicated field.",
    )

    def reset_iterative_refinement(self) -> None:
        """Reset the iterative refinement counter to zero."""
        self.iterative_refinement_iteration = 0
