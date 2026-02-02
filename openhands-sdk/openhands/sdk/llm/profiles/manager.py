import json
from functools import cache
from logging import getLogger
from pathlib import Path
from typing import Final

from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.profiles.config import ProfileMetadata, ProfilesConfig


_DEFAULT_PROFILE_DIR: Final[Path] = Path.home() / ".openhands" / "profiles"

logger = getLogger(__name__)


class ProfileManager:
    """
    Manages persistent LLM profiles stored on disk.

    Any change or modification to a profile store,i.e., a directory used to
    persistently store profiles, must be performed through this API. This ensures
    a consistent and reliable way to store, load, and update profiles.

    Note that the profile manager also uses a configuration file containing metadata
    about the profiles. This configuration file is primarily used to lazily load
    profiles from disk.
    """

    def __init__(self, base_dir: Path | str | None = None) -> None:
        """Initialize the profile manager.

        Args:
            base_dir: Path to the directory where the profiles are stored.
                If `None` is provided, the default directory is used, i.e.,
                `~/.openhands/profiles`.
        """
        self.base_dir = self._resolve_base_dir(base_dir)
        self.config_path = self.base_dir / "config.json"

        self._ensure_directories()
        self._ensure_config_file()

        self.config: ProfilesConfig = self._load_config()

    @staticmethod
    def _resolve_base_dir(base_dir: Path | str | None) -> Path:
        return Path(base_dir) if base_dir is not None else _DEFAULT_PROFILE_DIR

    def _ensure_directories(self) -> None:
        """Ensure that the directory exists."""
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_config_file(self) -> None:
        """Create an empty configuration if none is provided."""
        if not self.config_path.is_file():
            config = ProfilesConfig.empty()
            self.config_path.write_text(json.dumps(config.model_dump(), indent=2))

    def _load_config(self) -> ProfilesConfig:
        return ProfilesConfig.from_json(self.config_path)

    def get_profile_names(self) -> list[str]:
        """Return a list of available profile names."""
        return [profile.name for profile in self.config.profiles]

    def get_id_usages(self) -> list[str]:
        """Return a list of available usage ids."""
        return [
            profile.usage_id for profile in self.config.profiles if profile.usage_id
        ]

    def list_profiles(self) -> list[ProfileMetadata]:
        """Return a list of the metadata of the available profiles."""
        return self.config.profiles

    def load_profile(self, name: str) -> LLM:
        """
        Load an LLM instance from the given profile name.

        Args:
            name: Name of the profile to load.

        Returns:
            An LLM instance constructed from the profile configuration.

        Raises:
            FileNotFoundError: If the profile name does not exist.
        """
        profiles = self.config.profiles
        for profile in profiles:
            if name == profile.name:
                path = self.base_dir / profile.file
                llm_instance = LLM.load_from_json(str(path))
                logger.info(
                    f"[Profile Manager] Profile {name} successfully loaded from {path}"
                )
                return llm_instance

        raise FileNotFoundError(f"Unknown profile `{name}`")

    @cache
    def get_default_profile(self) -> LLM | None:
        """Return the default LLM profile."""
        default_profile_name = self.config.default_profile
        if default_profile_name is None:
            return None
        return self.load_profile(default_profile_name)

    def save_profile(
        self,
        name: str,
        description: str,
        llm: LLM,
        include_secrets: bool = True,
    ) -> None:
        """Save an LLM instance as a profile and update the configuration.

        Note that, if a profile with the given name already exists, it is overwritten.

        Args:
            name: Name of the profile to create or update.
            description: Description of the profile.
            llm: The LLM instance to persist.
            include_secrets: Whether to include secrets (e.g., API keys)
                when serializing the LLM configuration.
        """
        profile_filename = f"{name}.json"
        profile_filepath = self.base_dir / profile_filename

        profile_data = llm.model_dump(
            exclude_none=True,
            context={"expose_secrets": include_secrets},
        )
        profile_metadata = ProfileMetadata(
            name=name,
            file=profile_filename,
            description=description,
            usage_id=profile_data.get("usage_id", None),
        )

        profile_filepath.write_text(json.dumps(profile_data, indent=2))

        # update config
        for idx, existing in enumerate(self.config.profiles):
            if existing.name == name:
                self.config.profiles[idx] = profile_metadata
                break
        else:
            self.config.profiles.append(profile_metadata)
        self._write_config()

        logger.info(f"[Profile Manager] Saved profile `{name}` at {profile_filepath}")

    def _write_config(self) -> None:
        """The method update the config by re-writing it."""
        self.config_path.write_text(json.dumps(self.config.model_dump(), indent=2))

    def delete_profile(self, name: str) -> None:
        """Delete an existing profile and update the configuration.

        Note that the default profile cannot be deleted.

        Args:
            name: Name of the profile to delete.

        Raises:
            ValueError: If the profile does not exist or is the default profile.
        """
        if name not in self.get_profile_names():
            raise ValueError(f"Given profile `{name}` not present in profiles")
        if name == self.config.default_profile:
            raise ValueError(
                "Cannot delete the default profile. "
                "Set another profile as default first."
            )

        # remove file from system
        profile_path = (self.base_dir / name).with_suffix(".json")
        profile_path.unlink(missing_ok=True)

        # remove from config
        self.config.profiles = [p for p in self.config.profiles if p.name != name]
        self._write_config()

        logger.info(f"[Profile Manager] Successfully deleted profile `{name}`")

    def set_default_profile(self, name: str) -> None:
        """Set the default profile.

        Args:
            name: Name of the profile to set as the default.

        Raises:
            ValueError: If the profile name is not present in the configuration.
        """
        if name not in self.get_profile_names():
            raise ValueError(f"Given profile `{name}` not present in profiles")

        self.config.default_profile = name
        self._write_config()

        # invalidate cache
        self.get_default_profile.cache_clear()

        logger.info(f"[Profile Manager] Profile `{name}` set as default profile")
