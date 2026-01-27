import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from openhands.sdk.llm.llm import LLM
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


_SECRET_FIELDS: tuple[str, ...] = (
    "api_key",
    "aws_access_key_id",
    "aws_secret_access_key",
)
_DEFAULT_PROFILE_DIR = Path.home() / ".openhands" / "llm-profiles"

_PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class RegistryEvent(BaseModel):
    llm: LLM

    model_config: ClassVar[ConfigDict] = ConfigDict(
        arbitrary_types_allowed=True,
    )


DEFAULT_PROFILE_ID = "default"


class LLMRegistry:
    """A minimal LLM registry for managing LLM instances by usage ID.

    This registry provides a simple way to manage multiple LLM instances,
    avoiding the need to recreate LLMs with the same configuration.
    """

    registry_id: str
    retry_listener: Callable[[int, int], None] | None

    def __init__(
        self,
        retry_listener: Callable[[int, int], None] | None = None,
        profile_dir: str | Path | None = None,
    ):
        """Initialize the LLM registry.

        Args:
            retry_listener: Optional callback for retry events.
            profile_dir: Optional directory for persisted LLM profiles.
        """
        self.registry_id = str(uuid4())
        self.retry_listener = retry_listener
        self._usage_to_llm: dict[str, LLM] = {}
        self.subscriber: Callable[[RegistryEvent], None] | None = None
        self.profile_dir: Path = self._resolve_profile_dir(profile_dir)

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
    def usage_to_llm(self) -> dict[str, LLM]:
        """Access the internal usage-ID-to-LLM mapping."""

        return self._usage_to_llm

    def add(self, llm: LLM) -> None:
        """Add an LLM instance to the registry."""

        usage_id = llm.usage_id
        if usage_id in self._usage_to_llm:
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

    def _ensure_safe_profile_id(self, profile_id: str) -> str:
        if not profile_id or profile_id in {".", ".."}:
            raise ValueError("Invalid profile ID.")
        if Path(profile_id).name != profile_id:
            raise ValueError("Profile IDs cannot contain path separators.")
        if not _PROFILE_ID_PATTERN.fullmatch(profile_id):
            raise ValueError(
                "Profile IDs may only contain alphanumerics, '.', '_', or '-'."
            )
        return profile_id

    # ------------------------------------------------------------------
    # Profile management helpers
    # ------------------------------------------------------------------
    def list_profiles(self) -> list[str]:
        """List all profile IDs stored on disk."""

        return sorted(path.stem for path in self.profile_dir.glob("*.json"))

    def get_profile_path(self, profile_id: str) -> Path:
        """Return the path where profile_id is stored."""

        safe_id = self._ensure_safe_profile_id(profile_id)
        return self.profile_dir / f"{safe_id}.json"

    def load_profile(self, profile_id: str) -> LLM:
        """Load profile_id from disk and return an LLM."""

        path = self.get_profile_path(profile_id)
        if not path.exists():
            raise FileNotFoundError(f"Profile not found: {profile_id} -> {path}")
        llm = self._load_profile_with_synced_id(path, profile_id)
        logger.debug(f"Loaded profile {profile_id} from {path}")
        return llm

    def save_profile(
        self, profile_id: str, llm: LLM, include_secrets: bool = True
    ) -> Path:
        """Persist ``llm`` under ``profile_id``.

        By default, secrets are included in the saved JSON. Set
        ``include_secrets=False`` to omit secret fields.
        """

        safe_id = self._ensure_safe_profile_id(profile_id)
        path = self.get_profile_path(safe_id)
        existed_before = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = llm.model_dump(
            exclude_none=True,
            context={"expose_secrets": include_secrets},
        )
        data["profile_id"] = safe_id
        if not include_secrets:
            for secret_field in _SECRET_FIELDS:
                data.pop(secret_field, None)

        with path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        # Apply restrictive permissions when creating a new file
        if not existed_before:
            try:
                path.chmod(0o600)
            except Exception as e:  # best-effort on non-POSIX systems
                logger.debug(f"Unable to chmod profile file {path}: {e}")
        logger.info(f"Saved profile {safe_id} -> {path}")
        return path

    def validate_profile(self, data: Mapping[str, Any]) -> tuple[bool, list[str]]:
        """Return (is_valid, errors) after validating a profile payload."""

        try:
            LLM.model_validate(dict(data))
        except ValidationError as exc:
            messages: list[str] = []
            for error in exc.errors():
                loc = ".".join(str(piece) for piece in error.get("loc", ()))
                if loc:
                    messages.append(f"{loc}: {error.get('msg')}")
                else:
                    messages.append(error.get("msg", "Unknown validation error"))
            return False, messages
        return True, []

    # ------------------------------------------------------------------
    # Internal helper methods
    # ------------------------------------------------------------------
    def _resolve_profile_dir(self, profile_dir: str | Path | None) -> Path:
        if profile_dir is not None:
            return Path(profile_dir).expanduser()
        return _DEFAULT_PROFILE_DIR

    def _load_profile_with_synced_id(self, path: Path, profile_id: str) -> LLM:
        """Load an LLM profile while keeping profile metadata aligned.

        Most callers expect the loaded LLM to reflect the profile file name so the
        client apps can surface the active profile (e.g., in conversation history or CLI
        prompts).  We construct a *new* ``LLM`` via :meth:`model_copy` instead of
        mutating the loaded instance to respect the SDK's immutability
        conventions.

        We always align ``profile_id`` with the filename so callers get a precise
        view of which profile is active without mutating the on-disk payload. This
        mirrors previous behavior while avoiding in-place mutation.
        """

        llm = LLM.load_from_json(str(path))
        if llm.profile_id != profile_id:
            return llm.model_copy(update={"profile_id": profile_id})
        return llm

    def ensure_default_profile(self, llm: LLM) -> LLM:
        """Persist ``llm`` as a profile if it isn't already profiled.

        When an LLM instance without ``profile_id`` is used in a persisted
        conversation, we want the conversation to store a profile reference
        instead of embedding the full configuration inline.

        The profile is named after the LLM's ``usage_id`` (e.g., "agent",
        "condenser"), making it easy to identify which LLM config it represents.

        This helper creates or overwrites the profile in the profiles
        directory and returns a copy of ``llm`` with ``profile_id`` set.
        """

        if llm.profile_id:
            return llm

        profile_id = llm.usage_id or DEFAULT_PROFILE_ID
        self.save_profile(profile_id, llm)
        return llm.model_copy(update={"profile_id": profile_id})

    def get(self, usage_id: str) -> LLM:
        """Get an LLM instance from the registry.

        Args:
            usage_id: Unique identifier for the LLM usage slot.

        Returns:
            The LLM instance.

        Raises:
            KeyError: If usage_id is not found in the registry.
        """
        if usage_id not in self._usage_to_llm:
            raise KeyError(
                f"Usage ID '{usage_id}' not found in registry. "
                "Use add() to register an LLM first."
            )

        logger.info(
            f"[LLM registry {self.registry_id}]: Retrieved LLM for usage {usage_id}"
        )
        return self._usage_to_llm[usage_id]

    def list_usage_ids(self) -> list[str]:
        """List all registered usage IDs."""

        return list(self._usage_to_llm.keys())
