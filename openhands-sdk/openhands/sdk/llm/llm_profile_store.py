import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Final

from filelock import FileLock, Timeout
from pydantic import SecretStr

from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLM
    from openhands.sdk.utils.cipher import Cipher

_DEFAULT_PROFILE_DIR: Final[Path] = Path.home() / ".openhands" / "profiles"
_LOCK_TIMEOUT_SECONDS: Final[float] = 30.0
_SECRET_FIELD_NAMES: Final[tuple[str, ...]] = (
    "api_key",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
)
_ENCRYPTED_SECRET_PREFIX: Final[str] = "gAAAAA"

logger = get_logger(__name__)


def _looks_encrypted_secret(secret: SecretStr | None) -> bool:
    if secret is None:
        return False
    return secret.get_secret_value().startswith(_ENCRYPTED_SECRET_PREFIX)


class LLMProfileStore:
    """Standalone utility for persisting LLM configurations."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        """Initialize the profile store.

        Args:
            base_dir: Path to the directory where the profiles are stored.
                If `None` is provided, the default directory is used, i.e.,
                `~/.openhands/profiles`.
        """
        self.base_dir = Path(base_dir) if base_dir is not None else _DEFAULT_PROFILE_DIR
        # ensure directory existence
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._file_lock = FileLock(self.base_dir / ".profiles.lock")

    @contextmanager
    def _acquire_lock(self, timeout: float = _LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
        """Acquire file lock for safe concurrent access.

        Args:
            timeout: Maximum time to wait for lock acquisition in seconds.

        Raises:
            TimeoutError: If the lock cannot be acquired within the timeout.
        """
        try:
            with self._file_lock.acquire(timeout=timeout):
                yield
        except Timeout:
            logger.error(f"[Profile Store] Failed to acquire lock within {timeout}s")
            raise TimeoutError(
                f"Profile store lock acquisition timed out after {timeout}s"
            )

    def list(self) -> list[str]:
        """Returns a list of all profiles stored.

        Returns:
            List of profile filenames (e.g., ["default.json", "gpt4.json"]).
        """
        with self._acquire_lock():
            return [p.name for p in self.base_dir.glob("*.json")]

    def _get_profile_path(self, name: str) -> Path:
        """Get the full path for a profile name.

        Args:
            name: Profile name (must be a simple filename without path separators)

        Raises:
            ValueError: If name contains path separators or is invalid
        """
        # Remove .json extension if present for consistent handling
        clean_name = name.removesuffix(".json")

        # Validate: no path separators, not empty, no hidden files
        if (
            not clean_name
            or "/" in clean_name
            or "\\" in clean_name
            or clean_name.startswith(".")
        ):
            raise ValueError(
                f"Invalid profile name: {name!r}. "
                "Profile names must be simple filenames without path separators."
            )

        return self.base_dir / f"{clean_name}.json"

    def save(
        self,
        name: str,
        llm: "LLM",
        include_secrets: bool = False,
        cipher: "Cipher | None" = None,
    ) -> None:
        """Save a profile to the profile directory.

        Note that if a profile name already exists, it will be overwritten.

        Args:
            name: Name of the profile to save.
            llm: LLM instance to save
            include_secrets: Whether to include the profile secrets. Defaults to False.
            cipher: Optional cipher used to encrypt secrets at rest.

        Raises:
            TimeoutError: If the lock cannot be acquired.
        """
        profile_path = self._get_profile_path(name)

        with self._acquire_lock():
            if profile_path.exists():
                logger.info(
                    f"[Profile Store] Profile `{name}` already exists. Overwriting."
                )

            profile_json = llm.model_dump_json(
                exclude_none=True,
                indent=2,
                context={
                    "expose_secrets": include_secrets,
                    "cipher": cipher,
                },
            )
            with tempfile.NamedTemporaryFile(
                mode="w", dir=self.base_dir, suffix=".tmp", delete=False
            ) as tmp:
                tmp.write(profile_json)
                tmp_path = Path(tmp.name)

            Path.replace(tmp_path, profile_path)
            logger.info(f"[Profile Store] Saved profile `{name}` at {profile_path}")

    def load(self, name: str, cipher: "Cipher | None" = None) -> "LLM":
        """Load an LLM instance from the given profile name.

        Args:
            name: Name of the profile to load.
            cipher: Optional cipher used to decrypt persisted secrets.

        Returns:
            An LLM instance constructed from the profile configuration.

        Raises:
            FileNotFoundError: If the profile name does not exist.
            ValueError: If the profile file is corrupted or invalid.
            TimeoutError: If the lock cannot be acquired.
        """
        profile_path = self._get_profile_path(name)

        with self._acquire_lock():
            if not profile_path.exists():
                existing = [p.name for p in self.base_dir.glob("*.json")]
                raise FileNotFoundError(
                    f"Profile `{name}` not found. "
                    f"Available profiles: {', '.join(existing) or 'none'}"
                )

            try:
                from openhands.sdk.llm.llm import LLM

                llm_instance = LLM.model_validate_json(
                    profile_path.read_text(),
                    context={"cipher": cipher} if cipher else None,
                )
                if cipher is None and any(
                    _looks_encrypted_secret(getattr(llm_instance, field_name))
                    for field_name in _SECRET_FIELD_NAMES
                ):
                    raise ValueError(
                        f"Profile `{name}` contains encrypted secrets but no cipher "
                        "was provided"
                    )
            except Exception as e:
                # Re-raise as ValueError for clearer error handling
                raise ValueError(f"Failed to load profile `{name}`: {e}") from e

            logger.info(f"[Profile Store] Loaded profile `{name}` from {profile_path}")
            return llm_instance

    def delete(self, name: str) -> None:
        """Delete an existing profile.

        If the profile is not present in the profile directory, it does nothing.

        Args:
            name: Name of the profile to delete.

        Raises:
            TimeoutError: If the lock cannot be acquired.
        """
        profile_path = self._get_profile_path(name)

        with self._acquire_lock():
            if not profile_path.exists():
                logger.info(f"[Profile Store] Profile `{name}` not found. Skipping.")
                return

            profile_path.unlink()
            logger.info(f"[Profile Store] Deleted profile `{name}`")
