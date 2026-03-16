"""Service for managing persistent settings in agent-server.

This service provides CRUD operations for named LLM profiles, agent configurations,
and secret bundles, using file-based persistence similar to conversations.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from openhands.agent_server.settings_models import (
    NamedAgent,
    NamedLLMProfile,
    NamedSecrets,
)
from openhands.agent_server.utils import safe_rmtree, utc_now
from openhands.sdk.utils.cipher import Cipher


logger = logging.getLogger(__name__)

T = TypeVar("T", NamedLLMProfile, NamedAgent, NamedSecrets)


@dataclass
class SettingsService:
    """Service for managing persistent named settings.

    Settings are stored as JSON files in subdirectories:
    - {settings_dir}/llm_profiles/{name}.json
    - {settings_dir}/agents/{name}.json
    - {settings_dir}/secrets/{name}.json
    """

    settings_dir: Path
    cipher: Cipher | None = None
    _initialized: bool = field(default=False, init=False)

    # Cached settings (loaded on startup)
    _llm_profiles: dict[str, NamedLLMProfile] = field(default_factory=dict, init=False)
    _agents: dict[str, NamedAgent] = field(default_factory=dict, init=False)
    _secrets: dict[str, NamedSecrets] = field(default_factory=dict, init=False)

    @property
    def llm_profiles_dir(self) -> Path:
        return self.settings_dir / "llm_profiles"

    @property
    def agents_dir(self) -> Path:
        return self.settings_dir / "agents"

    @property
    def secrets_dir(self) -> Path:
        return self.settings_dir / "secrets"

    def _ensure_dirs(self) -> None:
        """Create settings directories if they don't exist."""
        self.settings_dir.mkdir(parents=True, exist_ok=True)
        self.llm_profiles_dir.mkdir(exist_ok=True)
        self.agents_dir.mkdir(exist_ok=True)
        self.secrets_dir.mkdir(exist_ok=True)

    def _load_item(self, path: Path, model_class: type[T], context: str) -> T | None:
        """Load a single item from a JSON file."""
        try:
            json_str = path.read_text()
            return model_class.model_validate_json(
                json_str,
                context={"cipher": self.cipher},
            )
        except Exception:
            logger.exception(f"Error loading {context} from {path}")
            return None

    def _save_item(self, item: BaseModel, path: Path, context: str) -> bool:
        """Save an item to a JSON file."""
        try:
            json_str = item.model_dump_json(
                indent=2,
                context={"cipher": self.cipher},
            )
            path.write_text(json_str)
            return True
        except Exception:
            logger.exception(f"Error saving {context} to {path}")
            return False

    def _load_all(self) -> None:
        """Load all settings from disk into memory."""
        # Load LLM profiles
        if self.llm_profiles_dir.exists():
            for file in self.llm_profiles_dir.glob("*.json"):
                profile = self._load_item(
                    file, NamedLLMProfile, f"LLM profile {file.stem}"
                )
                if profile:
                    self._llm_profiles[profile.name] = profile

        # Load agents
        if self.agents_dir.exists():
            for file in self.agents_dir.glob("*.json"):
                agent = self._load_item(file, NamedAgent, f"Agent {file.stem}")
                if agent:
                    self._agents[agent.name] = agent

        # Load secrets
        if self.secrets_dir.exists():
            for file in self.secrets_dir.glob("*.json"):
                secrets = self._load_item(file, NamedSecrets, f"Secrets {file.stem}")
                if secrets:
                    self._secrets[secrets.name] = secrets

        logger.info(
            f"Loaded {len(self._llm_profiles)} LLM profiles, "
            f"{len(self._agents)} agents, {len(self._secrets)} secret bundles"
        )

    # LLM Profile operations

    def list_llm_profiles(self) -> list[str]:
        """List all LLM profile names."""
        return list(self._llm_profiles.keys())

    def get_llm_profile(self, name: str) -> NamedLLMProfile | None:
        """Get an LLM profile by name."""
        return self._llm_profiles.get(name)

    def create_llm_profile(self, profile: NamedLLMProfile) -> bool:
        """Create or update an LLM profile."""
        # Update timestamp if it already exists
        if profile.name in self._llm_profiles:
            profile.updated_at = utc_now()

        path = self.llm_profiles_dir / f"{profile.name}.json"
        if self._save_item(profile, path, f"LLM profile {profile.name}"):
            self._llm_profiles[profile.name] = profile
            return True
        return False

    def delete_llm_profile(self, name: str) -> bool:
        """Delete an LLM profile by name."""
        if name not in self._llm_profiles:
            return False

        path = self.llm_profiles_dir / f"{name}.json"
        if safe_rmtree(path, f"LLM profile {name}"):
            del self._llm_profiles[name]
            return True
        return False

    # Agent operations

    def list_agents(self) -> list[str]:
        """List all agent names."""
        return list(self._agents.keys())

    def get_agent(self, name: str) -> NamedAgent | None:
        """Get an agent configuration by name."""
        return self._agents.get(name)

    def create_agent(self, agent: NamedAgent) -> bool:
        """Create or update an agent configuration."""
        if agent.name in self._agents:
            agent.updated_at = utc_now()

        path = self.agents_dir / f"{agent.name}.json"
        if self._save_item(agent, path, f"Agent {agent.name}"):
            self._agents[agent.name] = agent
            return True
        return False

    def delete_agent(self, name: str) -> bool:
        """Delete an agent configuration by name."""
        if name not in self._agents:
            return False

        path = self.agents_dir / f"{name}.json"
        if safe_rmtree(path, f"Agent {name}"):
            del self._agents[name]
            return True
        return False

    # Secrets operations

    def list_secrets(self) -> list[str]:
        """List all secrets bundle names."""
        return list(self._secrets.keys())

    def get_secrets(self, name: str) -> NamedSecrets | None:
        """Get a secrets bundle by name."""
        return self._secrets.get(name)

    def create_secrets(self, secrets: NamedSecrets) -> bool:
        """Create or update a secrets bundle."""
        if secrets.name in self._secrets:
            secrets.updated_at = utc_now()

        path = self.secrets_dir / f"{secrets.name}.json"
        if self._save_item(secrets, path, f"Secrets {secrets.name}"):
            self._secrets[secrets.name] = secrets
            return True
        return False

    def delete_secrets(self, name: str) -> bool:
        """Delete a secrets bundle by name."""
        if name not in self._secrets:
            return False

        path = self.secrets_dir / f"{name}.json"
        if safe_rmtree(path, f"Secrets {name}"):
            del self._secrets[name]
            return True
        return False

    # Lifecycle methods

    async def __aenter__(self) -> "SettingsService":
        """Initialize the service and load settings from disk."""
        self._ensure_dirs()
        self._load_all()
        self._initialized = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Clean up resources."""
        self._initialized = False

    @classmethod
    def get_instance(
        cls, settings_dir: Path, cipher: Cipher | None = None
    ) -> "SettingsService":
        """Create a new SettingsService instance."""
        return cls(settings_dir=settings_dir, cipher=cipher)


# Global singleton
_settings_service: SettingsService | None = None


def get_default_settings_service() -> SettingsService:
    """Get the default settings service singleton."""
    global _settings_service
    if _settings_service is not None:
        return _settings_service

    from openhands.agent_server.config import get_default_config

    config = get_default_config()
    _settings_service = SettingsService.get_instance(
        settings_dir=config.settings_path,
        cipher=config.cipher,
    )
    return _settings_service
