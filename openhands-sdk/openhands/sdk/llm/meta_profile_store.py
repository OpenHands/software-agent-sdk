"""JSON-file storage for meta-profiles under ``~/.openhands/meta-profiles``.

Key invariant: every model reference in a meta-profile (``classifier_model``,
``default_model``, and each class's ``model``) is the *name of a saved LLM
profile* in :class:`~openhands.sdk.llm.llm_profile_store.LLMProfileStore`, not a
raw model string — credentials/provider settings resolve through that store.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

from filelock import FileLock, Timeout
from pydantic import BaseModel, Field

from openhands.sdk.llm.llm_profile_store import PROFILE_NAME_REGEX
from openhands.sdk.logger import get_logger


_DEFAULT_META_PROFILE_DIR: Final[Path] = Path.home() / ".openhands" / "meta-profiles"
_LOCK_TIMEOUT_SECONDS: Final[float] = 30.0

logger = get_logger(__name__)


class MetaProfileLimitExceeded(Exception):
    """Raised when saving would exceed the configured meta-profile limit."""


class MetaProfileClass(BaseModel):
    """A single task category and the LLM profile that should handle it."""

    description: str = Field(
        description="Natural-language description of the kind of task this "
        "class covers (e.g. 'task is UI oriented or requires looking at images')."
    )
    model: str = Field(
        description="Name of the saved LLM profile to switch to for tasks "
        "matching this class."
    )


class MetaProfile(BaseModel):
    """A declarative model-routing configuration."""

    classifier_model: str = Field(
        description="Name of the saved LLM profile used to classify the task."
    )
    default_model: str = Field(
        description="Name of the saved LLM profile to use when no class matches."
    )
    classes: list[MetaProfileClass] = Field(
        default_factory=list,
        description="Ordered list of task classes and their target profiles.",
    )


class MetaProfileStore:
    """Read meta-profiles from ``~/.openhands/meta-profiles`` (by default)."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        """Initialize the meta-profile store.

        Args:
            base_dir: Directory where meta-profiles are stored. Defaults to
                ``~/.openhands/meta-profiles`` when ``None``.
        """
        self.base_dir = (
            Path(base_dir) if base_dir is not None else _DEFAULT_META_PROFILE_DIR
        )
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._file_lock = FileLock(self.base_dir / ".meta-profiles.lock")

    @contextmanager
    def _acquire_lock(self, timeout: float = _LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
        """Acquire the file lock for safe concurrent access.

        The lock is reentrant within a process, so methods that hold it may
        call other locked methods (e.g. ``save`` calls ``list``).

        Raises:
            TimeoutError: If the lock cannot be acquired within ``timeout``.
        """
        try:
            with self._file_lock.acquire(timeout=timeout):
                yield
        except Timeout:
            logger.error(
                f"[Meta-profile Store] Failed to acquire lock within {timeout}s"
            )
            raise TimeoutError(
                f"Meta-profile store lock acquisition timed out after {timeout}s"
            )

    def list(self) -> list[str]:
        """Return stored meta-profile names (without ``.json``), sorted.

        The order is alphabetical, not chronological. Callers that fall back to
        "the first available" meta-profile (when none is explicitly active) get
        the alphabetically-first name, which is stable but not "most recent".
        """
        return sorted(
            p.stem
            for p in self.base_dir.glob("*.json")
            if PROFILE_NAME_REGEX.match(p.stem)
        )

    def _get_path(self, name: str) -> Path:
        clean_name = name.removesuffix(".json")
        if not PROFILE_NAME_REGEX.match(clean_name):
            raise ValueError(
                f"Invalid meta-profile name: {name!r}. "
                "Names must be 1-64 characters, start with a letter or digit, "
                "and contain only letters, digits, '.', '_', or '-'."
            )
        return self.base_dir / f"{clean_name}.json"

    def load(self, name: str) -> MetaProfile:
        """Load a meta-profile by name.

        Raises:
            FileNotFoundError: If the meta-profile does not exist.
            ValueError: If the file is corrupted or fails validation.
        """
        path = self._get_path(name)
        if not path.exists():
            existing = self.list()
            raise FileNotFoundError(
                f"Meta-profile `{name}` not found. "
                f"Available meta-profiles: {', '.join(existing) or 'none'}"
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return MetaProfile.model_validate(data)
        except Exception as e:
            raise ValueError(f"Failed to load meta-profile `{name}`: {e}") from e

    def save(
        self,
        name: str,
        meta_profile: MetaProfile,
        *,
        max_profiles: int | None = None,
    ) -> None:
        """Persist a meta-profile under ``name`` (atomic write, overwrites).

        Args:
            name: Name of the meta-profile to save.
            meta_profile: The meta-profile to persist.
            max_profiles: Optional cap on the number of meta-profiles. When set,
                raises :class:`MetaProfileLimitExceeded` if creating a *new*
                meta-profile would exceed the limit.

        Raises:
            MetaProfileLimitExceeded: If ``max_profiles`` would be exceeded.
            ValueError: If ``name`` is not a valid meta-profile name.
        """
        path = self._get_path(name)

        # Hold the lock across the precondition check and the atomic replace so
        # concurrent creators cannot both pass the limit check and overshoot
        # ``max_profiles`` (TOCTOU), mirroring ``LLMProfileStore``.
        with self._acquire_lock():
            if max_profiles is not None and not path.exists():
                if len(self.list()) >= max_profiles:
                    raise MetaProfileLimitExceeded(
                        f"Meta-profile limit reached ({max_profiles})."
                    )

            profile_json = json.dumps(meta_profile.model_dump(mode="json"), indent=2)
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=self.base_dir,
                suffix=".tmp",
                delete=False,
                encoding="utf-8",
            ) as tmp:
                tmp.write(profile_json)
                tmp_path = Path(tmp.name)

            try:
                Path.replace(tmp_path, path)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
        logger.info(f"Saved meta-profile `{name}` at {path}")

    def delete(self, name: str) -> None:
        """Delete a meta-profile (idempotent — missing names are a no-op).

        Raises:
            ValueError: If ``name`` is not a valid meta-profile name.
        """
        path = self._get_path(name)
        with self._acquire_lock():
            if not path.exists():
                logger.info(f"Meta-profile `{name}` not found. Skipping delete.")
                return
            path.unlink()
        logger.info(f"Deleted meta-profile `{name}`")

    def list_summaries(self) -> list[dict[str, Any]]:
        """List meta-profile metadata without full schema validation.

        Files with corrupted JSON or non-dict top-level values are skipped with
        a warning so a single bad file never breaks the listing.
        """
        summaries: list[dict[str, Any]] = []
        for name in self.list():
            try:
                data = json.loads(self._get_path(name).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Skipping corrupted meta-profile {name!r}: {e}")
                continue
            if not isinstance(data, dict):
                logger.warning(f"Skipping non-dict meta-profile {name!r}")
                continue
            classes = data.get("classes") or []
            summaries.append(
                {
                    "name": name,
                    "classifier_model": data.get("classifier_model"),
                    "default_model": data.get("default_model"),
                    "num_classes": len(classes) if isinstance(classes, list) else 0,
                }
            )
        return summaries
