from collections.abc import Callable
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.profiles.manager import ProfileManager
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.deprecation import deprecated


logger = get_logger(__name__)


class RegistryEvent(BaseModel):
    llm: LLM

    model_config: ClassVar[ConfigDict] = ConfigDict(
        arbitrary_types_allowed=True,
    )


class LLMRegistry:
    """A minimal LLM registry for managing LLM instances by usage ID.

    This registry provides a simple way to manage multiple LLM instances,
    avoiding the need to recreate LLMs with the same configuration.
    """

    registry_id: str
    retry_listener: Callable[[int, int], None] | None

    def __init__(
        self,
        load_from_disk: bool = False,
        profile_manager_path: Path | str | None = None,
        retry_listener: Callable[[int, int], None] | None = None,
    ) -> None:
        """Initialize the LLM registry.

        Args:
            load_from_disk: Whether to load existing profiles from disk.
            profile_manager_path: Path to the profile directory.
                If nothing is provided, the default path `.openhands/profiles` is used.
            retry_listener: Optional callback for retry events.
        """
        self.registry_id = str(uuid4())
        self.profile_manager: ProfileManager | None = (
            ProfileManager(profile_manager_path) if load_from_disk else None
        )
        self.retry_listener = retry_listener

        self._usage_to_llm: dict[str, LLM] = {}
        self.subscriber: Callable[[RegistryEvent], None] | None = None

    def subscribe(self, callback: Callable[[RegistryEvent], None]) -> None:
        """Subscribe to registry events.

        Args:
            callback: Function to call when LLMs are created or updated.
        """
        self.subscriber = callback

    def notify(self, event: RegistryEvent) -> None:
        """Notify subscribers of registry events.

        Args:
            event: The registry event to notify about.
        """
        if self.subscriber:
            try:
                self.subscriber(event)
            except Exception as e:
                logger.warning(f"Failed to emit event: {e}")

    @property
    @deprecated(
        deprecated_in="1.10.0",
        removed_in="1.20.0",
        details="The method is deprecated and will be removed in next versions",
    )
    def usage_to_llm(self) -> dict[str, LLM]:
        """Access the internal usage-ID-to-LLM mapping."""

        return self._usage_to_llm

    def add(self, llm: LLM) -> None:
        """Add an LLM instance to the registry.

        Args:
            llm: The LLM instance to register.

        Raises:
            ValueError: If llm.usage_id already exists in the registry.
        """
        usage_id = llm.usage_id
        if usage_id in self.list_usage_ids():
            message = (
                f"Usage ID '{usage_id}' already exists in registry. "
                "Use a different usage_id on the LLM or "
                "call get() to retrieve the existing LLM."
            )
            raise ValueError(message)

        self._usage_to_llm[usage_id] = llm
        self.notify(RegistryEvent(llm=llm))
        logger.debug(
            f"[LLM registry {self.registry_id}]: Added LLM for usage {usage_id}"
        )

    def get(self, usage_id: str) -> LLM:
        """Get an LLM instance from the registry.

        Args:
            usage_id: Unique identifier for the LLM usage slot.

        Returns:
            The LLM instance.

        Raises:
            KeyError: If usage_id is not found in the registry.
        """
        llm_instance: LLM | None = None
        if usage_id in self._usage_to_llm:
            llm_instance = self._usage_to_llm[usage_id]
        elif self.profile_manager and usage_id in self.profile_manager.get_id_usages():
            profile_name = next(
                p.name
                for p in self.profile_manager.config.profiles
                if p.usage_id == usage_id
            )
            llm_instance = self.profile_manager.load_profile(profile_name)
            self._usage_to_llm[usage_id] = llm_instance
        else:
            raise KeyError(
                f"Usage ID '{usage_id}' not found in registry. "
                "Use add() to register an LLM first."
            )

        logger.info(
            f"[LLM registry {self.registry_id}]: Retrieved LLM for usage {usage_id}"
        )
        return llm_instance

    def list_usage_ids(self) -> list[str]:
        """List all registered usage IDs."""
        in_memory_usage_ids = set(self._usage_to_llm.keys())
        on_disk_usage_ids = (
            set(self.profile_manager.get_id_usages())
            if self.profile_manager is not None
            else set()
        )

        return sorted(in_memory_usage_ids.union(on_disk_usage_ids))

    def export_profiles(
        self, path: Path | str | None = None, include_secrets: bool = True
    ) -> None:
        """
        Export the profiles contained in the registry to the specified path.
        Note that this method will overwrite existing profiles if they already exist.

        If the path is not provided (e.g., `None`) and the `load_from_disk` flag is
        true, the registry is written to the current profile directory. Otherwise,
        it is written to the current working directory.

        Args:
            path: Path where the profiles are saved. If the path is not provided
                (e.g., `None`) and the `load_from_disk` flag is true, the registry
                is written to the current profile directory. Otherwise, it is
                written to the current working directory.
            include_secrets: Whether to include secrets in the profile files.
        """
        if self.profile_manager and path is None:
            profile_dir = self.profile_manager.base_dir
            profile_manager = self.profile_manager
        else:
            profile_dir = Path(path) if path else Path.cwd()
            profile_manager = ProfileManager(profile_dir)

        for usage_id, llm_instance in self._usage_to_llm.items():
            profile_manager.save_profile(
                name=llm_instance.model,
                llm=llm_instance,
                description=usage_id,
                include_secrets=include_secrets,
            )

        logger.info(
            f"[LLM registry {self.registry_id}]: "
            f"Register successfully exported in {profile_dir}"
        )
