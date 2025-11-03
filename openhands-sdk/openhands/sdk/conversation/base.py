from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.conversation.events_list_base import EventsListBase
from openhands.sdk.conversation.secret_registry import SecretValue
from openhands.sdk.conversation.types import ConversationCallbackType, ConversationID
from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.message import Message
from openhands.sdk.security.confirmation_policy import (
    ConfirmationPolicyBase,
    NeverConfirm,
)
from openhands.sdk.workspace.base import BaseWorkspace


if TYPE_CHECKING:
    from openhands.sdk.agent.base import AgentBase
    from openhands.sdk.conversation.state import AgentExecutionStatus


class ConversationStateProtocol(Protocol):
    """Protocol defining the interface for conversation state objects."""

    @property
    def id(self) -> ConversationID:
        """The conversation ID."""
        ...

    @property
    def events(self) -> EventsListBase:
        """Access to the events list."""
        ...

    @property
    def agent_status(self) -> "AgentExecutionStatus":
        """The current agent execution status."""
        ...

    @property
    def confirmation_policy(self) -> ConfirmationPolicyBase:
        """The confirmation policy."""
        ...

    @property
    def activated_knowledge_skills(self) -> list[str]:
        """List of activated knowledge skills."""
        ...

    @property
    def workspace(self) -> BaseWorkspace:
        """The workspace for agent operations and tool execution."""
        ...

    @property
    def persistence_dir(self) -> str | None:
        """The persistence directory from the FileStore.

        If None, it means the conversation is not being persisted.
        """
        ...

    @property
    def agent(self) -> "AgentBase":
        """The agent running in the conversation."""
        ...


class BaseConversation(ABC):
    """Abstract base class for conversation implementations.

    This class defines the interface that all conversation implementations must follow.
    Conversations manage the interaction between users and agents, handling message
    exchange, execution control, and state management.
    """

    @property
    @abstractmethod
    def id(self) -> ConversationID: ...

    @property
    @abstractmethod
    def state(self) -> ConversationStateProtocol: ...

    @property
    @abstractmethod
    def conversation_stats(self) -> ConversationStats: ...

    @abstractmethod
    def send_message(self, message: str | Message) -> None:
        """Send a message to the agent."""
        ...

    @abstractmethod
    def run(self) -> None:
        """Execute the agent to process messages and perform actions.

        This method runs the agent until it finishes processing the current
        message or reaches the maximum iteration limit.
        """
        ...

    @abstractmethod
    def set_confirmation_policy(self, policy: ConfirmationPolicyBase) -> None:
        """Set the confirmation policy for the conversation."""
        ...

    @property
    def confirmation_policy_active(self) -> bool:
        return not isinstance(self.state.confirmation_policy, NeverConfirm)

    @property
    def is_confirmation_mode_active(self) -> bool:
        """Check if confirmation mode is active.

        Returns True if BOTH conditions are met:
        1. The agent has a security analyzer set (not None)
        2. The confirmation policy is active

        """
        return (
            self.state.agent.security_analyzer is not None
            and self.confirmation_policy_active
        )

    @abstractmethod
    def reject_pending_actions(
        self, reason: str = "User rejected the action"
    ) -> None: ...

    @abstractmethod
    def pause(self) -> None: ...

    @abstractmethod
    def update_secrets(self, secrets: Mapping[str, SecretValue]) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def generate_title(self, llm: LLM | None = None, max_length: int = 50) -> str:
        """Generate a title for the conversation based on the first user message.

        Args:
            llm: Optional LLM to use for title generation. If not provided,
                 uses the agent's LLM.
            max_length: Maximum length of the generated title.

        Returns:
            A generated title for the conversation.

        Raises:
            ValueError: If no user messages are found in the conversation.
        """
        ...

    @staticmethod
    def get_persistence_dir(
        persistence_base_dir: str, conversation_id: ConversationID
    ) -> str:
        """Get the persistence directory for the conversation."""
        return str(Path(persistence_base_dir) / conversation_id.hex)

    @staticmethod
    def compose_callbacks(
        callbacks: Iterable[ConversationCallbackType],
    ) -> ConversationCallbackType:
        """Compose multiple callbacks into a single callback function.

        Args:
            callbacks: An iterable of callback functions

        Returns:
            A single callback function that calls all provided callbacks
        """

        def composed(event) -> None:
            for cb in callbacks:
                if cb:
                    cb(event)

        return composed
