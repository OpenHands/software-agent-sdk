"""Agent-specific runtime state registry that persists across iterations."""

from typing import Any

from pydantic import Field

from openhands.sdk.utils.models import OpenHandsModel


# Registry keys for agent state
ITERATIVE_REFINEMENT_ITERATION_KEY = "iterative_refinement_iteration"


class AgentStateRegistry(OpenHandsModel):
    """Registry for agent-specific runtime state that persists across iterations.

    This class provides a flexible, loosely-coupled storage mechanism for
    agent-specific state. Instead of embedding typed fields for each feature,
    agents store their state using string keys in a dictionary.

    This design:
    - Decouples agent-specific state from conversation-level concerns
    - Allows different agents to store different state without modifying this class
    - Makes it easier to compose agents with different capabilities
    - Avoids the class becoming a dumping ground for unrelated state

    The registry is embedded within ConversationState and is automatically
    persisted along with the conversation.

    Example:
        # Store state
        registry = state.agent_state_registry
        registry.set("my_feature_counter", 5)

        # Retrieve state with default
        counter = registry.get("my_feature_counter", 0)

        # Check if key exists
        if registry.has("my_feature_counter"):
            ...

    Predefined keys:
        - ITERATIVE_REFINEMENT_ITERATION_KEY: Current iteration count for critic
          iterative refinement.
    """

    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Dictionary storing agent-specific state. Keys are feature "
        "identifiers, values are feature-specific state data.",
    )

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the registry.

        Args:
            key: The key to look up.
            default: Value to return if key is not found.

        Returns:
            The stored value, or default if not found.
        """
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a value in the registry.

        Args:
            key: The key to store under.
            value: The value to store.
        """
        self.data[key] = value

    def has(self, key: str) -> bool:
        """Check if a key exists in the registry.

        Args:
            key: The key to check.

        Returns:
            True if the key exists, False otherwise.
        """
        return key in self.data

    def remove(self, key: str) -> Any | None:
        """Remove a key from the registry.

        Args:
            key: The key to remove.

        Returns:
            The removed value, or None if key didn't exist.
        """
        return self.data.pop(key, None)

    def clear(self) -> None:
        """Clear all state from the registry."""
        self.data.clear()


# Backward compatibility alias
AgentState = AgentStateRegistry
