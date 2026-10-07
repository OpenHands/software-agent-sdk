"""User-editable conversation runtime settings.

Canvas writes them to ``misc_settings.runtime`` through ``PATCH /api/settings``;
they are read where they apply, so a change takes effect without a restart.
An unset field keeps the server's startup ``Config`` value.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openhands.agent_server.config import Config
from openhands.agent_server.launch import container_browser_enabled
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

MISC_NAMESPACE = "runtime"


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    docker_retention_days: float | None = Field(default=None, gt=0)
    docker_disk_budget: float | None = Field(default=None, gt=0, lt=1)
    docker_idle_stop_minutes: float | None = Field(default=None, gt=0)
    docker_memory: str | None = None
    docker_cpus: float | None = Field(default=None, gt=0)
    docker_browser: bool | None = None
    worktree_retention_days: float | None = Field(default=None, gt=0)
    worktree_remove_on_delete: bool = False
    worktree_delete_branch: bool = False
    worktree_base: Literal["default_branch", "head"] = "default_branch"

    def docker_storage_policy(
        self, config: Config
    ) -> tuple[float | None, float | None]:
        """(disk budget, retention days) for the Docker reclaimer."""
        return (
            self.docker_disk_budget or config.conversation_storage_disk_budget,
            self.docker_retention_days or config.conversation_storage_retention_days,
        )

    def docker_idle_ttl_seconds(self, config: Config) -> float | None:
        if self.docker_idle_stop_minutes:
            return self.docker_idle_stop_minutes * 60
        return config.conversation_idle_ttl_seconds

    def docker_browser_enabled(self, config: Config) -> bool:
        # Only narrows: an image without chromium cannot gain a browser.
        return container_browser_enabled(config) and self.docker_browser is not False


def load_runtime_settings() -> RuntimeSettings:
    from openhands.agent_server.persistence import get_settings_store

    try:
        settings = get_settings_store().load()
    except Exception:
        logger.warning("Could not load runtime settings", exc_info=True)
        return RuntimeSettings()
    raw = settings.misc_settings.get(MISC_NAMESPACE) if settings else None
    try:
        return RuntimeSettings.model_validate(raw or {})
    except ValidationError:
        logger.warning("Ignoring invalid misc_settings.%s", MISC_NAMESPACE)
        return RuntimeSettings()
