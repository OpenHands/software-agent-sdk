"""Read-only capacity reporting for server-managed runtime storage."""

import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class ManagedStorageUsage(BaseModel):
    scope: Literal["docker_runtime_filesystem"] = "docker_runtime_filesystem"
    free_bytes: int
    usage_fraction: float = Field(
        description=(
            "Used / (used + available) on the backing filesystem, not directory size."
        )
    )
    disk_budget: float | None = Field(
        description="Configured filesystem usage fraction that triggers reclamation."
    )
    retention_days: float | None


class StorageStatus(BaseModel):
    managed_storage: ManagedStorageUsage | None = Field(
        default=None,
        description=(
            "Null when this server has no managed runtime storage. "
            "Local workspaces are not included."
        ),
    )


def read_storage_usage(
    root: Path, disk_budget: float | None, retention_days: float | None
) -> StorageStatus:
    """One filesystem capacity query, with no directory walk or maintenance."""
    usage = shutil.disk_usage(root)
    return StorageStatus(
        managed_storage=ManagedStorageUsage(
            free_bytes=usage.free,
            usage_fraction=usage.used / (usage.used + usage.free),
            disk_budget=disk_budget,
            retention_days=retention_days,
        )
    )
