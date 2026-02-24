from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.llm_registry import LLMRegistry
from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk.agent.base import AgentBase
    from openhands.sdk.llm import LLM

logger = get_logger(__name__)


@dataclass
class SwitchModelHandler:
    """Standalone handler for /model command parsing, switching, and info."""

    profile_store: LLMProfileStore
    llm_registry: LLMRegistry

    @staticmethod
    def parse(text: str) -> tuple[str, str | None] | None:
        """Parse a /model command from user text.

        Returns:
            None if the text is not a /model command.
            ("", None) for bare "/model" (info request).
            ("profile_name", remaining_text_or_None) otherwise.
        """
        stripped = text.strip()
        if stripped == "/model":
            return "", None
        if not stripped.startswith("/model "):
            return None
        rest = stripped[len("/model ") :].strip()
        if not rest:
            return "", None
        parts = rest.split(None, 1)
        profile_name = parts[0]
        remaining = parts[1] if len(parts) > 1 else None
        return profile_name, remaining

    def switch(self, agent: "AgentBase", profile_name: str) -> "AgentBase":
        """Load a model profile and return a new agent with the swapped LLM.

        The caller is responsible for storing the returned agent and updating
        any external state (e.g. ConversationState).

        Args:
            agent: Current agent instance.
            profile_name: Name of the profile to load from LLMProfileStore.

        Returns:
            A new AgentBase instance with the switched LLM.

        Raises:
            FileNotFoundError: If the profile does not exist.
            ValueError: If the profile is corrupted or invalid.
        """
        if profile_name in self.llm_registry.list_usage_ids():
            new_llm = self.llm_registry.get(profile_name)
        else:
            new_llm = self.profile_store.load(profile_name)
            new_llm = new_llm.model_copy(update={"usage_id": profile_name})
            self.llm_registry.add(new_llm)

        return agent.model_copy(update={"llm": new_llm})

    def profiles_info(self, llm: "LLM") -> str:
        """Return a string with current model and available profiles.

        The caller is responsible for emitting the string as an event.
        """
        current_model = llm.model
        stored_profiles = self.profile_store.list()
        registry_profiles = self.llm_registry.list_usage_ids()
        profile_names = list(set(stored_profiles).union(set(registry_profiles)))
        profile_list = (
            ", ".join(sorted([Path(p).stem for p in profile_names]))
            if profile_names
            else "[]"
        )
        return f"Current model: {current_model}\nAvailable profiles: {profile_list}"
