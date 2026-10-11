from enum import Enum
from pathlib import Path

from pydantic import BaseModel, field_serializer

from openhands.sdk.utils.path import to_posix_path


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
        return to_posix_path(path)


class GitDiff(BaseModel):
    modified: str | None
    original: str | None


class GitCommit(BaseModel):
    sha: str
    short_sha: str
    subject: str
    author: str
    # ISO 8601 author date with UTC offset (git log format %aI).
    timestamp: str


class GitCommitsPage(BaseModel):
    commits: list[GitCommit]
    has_more: bool


class GitProviderRepository(BaseModel):
    id: str
    full_name: str
    git_provider: str
    is_public: bool
    stargazers_count: int | None = None
    pushed_at: str | None = None
    main_branch: str | None = None


class GitProviderRepositoryPage(BaseModel):
    items: list[GitProviderRepository]
    next_page_id: str | None = None
    missing_token: bool = False
