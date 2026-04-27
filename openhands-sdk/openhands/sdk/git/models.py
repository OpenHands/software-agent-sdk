from enum import Enum
from pathlib import Path

from pydantic import BaseModel, field_serializer


class GitChangeStatus(Enum):
    MOVED = "MOVED"
    ADDED = "ADDED"
    DELETED = "DELETED"
    UPDATED = "UPDATED"


class GitChange(BaseModel):
    status: GitChangeStatus
    path: Path

    @field_serializer("path", when_used="json")
    def _serialize_path(self, path: Path) -> str:
        return path.as_posix()


class GitDiff(BaseModel):
    modified: str | None
    original: str | None
