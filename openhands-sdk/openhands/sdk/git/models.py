from enum import Enum, StrEnum
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


class GitPullRequestState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


class GitPullRequestChecks(StrEnum):
    """Combined CI result of a pull request's head commit."""

    SUCCESS = "success"
    FAILURE = "failure"
    PENDING = "pending"
    NONE = "none"


class GitPullRequestStatus(BaseModel):
    """Live state of one pull request on a git provider, for status badges."""

    git_provider: str
    repository: str
    number: int
    state: GitPullRequestState
    draft: bool = False
    title: str
    url: str
    head_sha: str | None = None
    checks: GitPullRequestChecks
