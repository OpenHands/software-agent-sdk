"""Git router for OpenHands SDK."""

import asyncio
import base64
import functools
import logging
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Path as PathParam, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openhands.agent_server._secrets_exposure import get_config
from openhands.agent_server.git_provider_service import (
    GitProviderAPIError,
    UnsupportedGitProviderError,
    search_provider_repositories,
)
from openhands.agent_server.persistence import (
    SECRET_NAME_PATTERN,
    FileSecretsStore,
    get_secrets_store,
)
from openhands.agent_server.server_details_router import update_last_execution_time
from openhands.sdk.git.exceptions import GitError, GitRepositoryError
from openhands.sdk.git.git_changes import get_git_changes
from openhands.sdk.git.git_commits import (
    get_commit_changes,
    get_commit_file_diff,
    get_git_commits,
)
from openhands.sdk.git.git_diff import get_git_diff
from openhands.sdk.git.models import (
    GitChange,
    GitCommitsPage,
    GitDiff,
    GitProviderRepositoryPage,
)
from openhands.sdk.workspace.repo import GitProvider


git_router = APIRouter(prefix="/git", tags=["Git"])
runtime_git_router = APIRouter(prefix="/git", tags=["Git"])
logger = logging.getLogger(__name__)


_REF_QUERY_DESCRIPTION = (
    "Optional git ref to diff against (e.g. 'HEAD' for git status-style "
    "changes, or a commit hash). When omitted, the upstream/default branch "
    "is auto-detected."
)

_COMMIT_QUERY_DESCRIPTION = (
    "Optional commit SHA. When set, the diff is the change that commit "
    "introduced (vs its first parent), read entirely from git objects so "
    "deleted files still render. Mutually exclusive with 'ref'."
)

# Hex-only so a path/query value can never reach git argv as an option
# (list-args protect against shell injection, not option injection).
_SHA_PATTERN = r"^[0-9a-fA-F]{4,64}$"

RepositoryProvider = Literal["github", "gitlab", "bitbucket"]
RepositoryValidationStatus = Literal[
    "accessible", "missing_credentials", "denied", "not_found", "unavailable"
]

_MAX_CREDENTIAL_NAMES = 5
_PROVIDER_TIMEOUT_SECONDS = 5.0
_PROVIDER_API_BASE_URLS: dict[RepositoryProvider, str] = {
    "github": "https://api.github.com",
    "gitlab": "https://gitlab.com/api/v4",
    "bitbucket": "https://api.bitbucket.org/2.0",
}


class ValidateRepositoryRequest(BaseModel):
    """Bounded input for checking access to a hosted Git repository."""

    model_config = ConfigDict(extra="forbid")

    provider: RepositoryProvider
    repository: str = Field(
        min_length=3,
        max_length=255,
        description="Provider-local owner/repository, including subgroups on GitLab.",
    )
    ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Branch, tag, or commit to check through the provider API.",
    )
    credential_names: list[str] = Field(
        default_factory=list,
        max_length=_MAX_CREDENTIAL_NAMES,
        description=(
            "Stored secret names in priority order; the first nonempty value is used. "
            "An empty list checks anonymous access."
        ),
    )

    @field_validator("repository")
    @classmethod
    def _validate_repository(cls, repository: str) -> str:
        components = repository.split("/")
        if any(
            not component
            or component in {".", ".."}
            or any(
                ord(character) == 0x20
                or ord(character) < 0x20
                or ord(character) == 0x7F
                or not (character.isalnum() or character in "_.-")
                for character in component
            )
            for component in components
        ):
            raise ValueError("repository must be a provider repository identifier")
        return repository

    @field_validator("ref")
    @classmethod
    def _validate_ref(cls, ref: str | None) -> str | None:
        if ref is not None and not _is_valid_git_ref(ref):
            raise ValueError("ref must be a bounded git ref")
        return ref

    @field_validator("credential_names")
    @classmethod
    def _validate_credential_names(cls, credential_names: list[str]) -> list[str]:
        if len(set(credential_names)) != len(credential_names):
            raise ValueError("credential_names must not contain duplicates")
        if not all(SECRET_NAME_PATTERN.fullmatch(name) for name in credential_names):
            raise ValueError("credential_names must be valid secret names")
        return credential_names

    @model_validator(mode="after")
    def _validate_provider_repository_shape(self) -> Self:
        if self.provider in {"github", "bitbucket"} and self.repository.count("/") != 1:
            raise ValueError(
                "repository must have owner/repository form for this provider"
            )
        return self


class ValidateRepositoryResponse(BaseModel):
    """Sanitized provider API verdict, not a guarantee that git clone will work.

    Providers may return ``not_found`` for private repositories. ``unavailable``
    means the check is inconclusive, including redirects, throttling, and transport
    failures; it does not imply that retrying will succeed.
    """

    status: RepositoryValidationStatus


def _is_valid_git_ref(ref: str) -> bool:
    """Validate Git's ref-name rules before putting a ref in a URL path."""
    if not ref or ref == "@":
        return False
    if (
        ref.startswith("/")
        or ref.endswith("/")
        or "//" in ref
        or ".." in ref
        or "@{" in ref
        or ref.endswith(".")
    ):
        return False
    if any(
        ord(character) == 0x20
        or ord(character) < 0x20
        or ord(character) == 0x7F
        or character in "~^:?*[\\"
        for character in ref
    ):
        return False
    return all(
        not component.startswith(".") and not component.endswith(".lock")
        for component in ref.split("/")
    )


def _provider_repository_url(
    provider: RepositoryProvider, repository: str, ref: str | None
) -> str:
    """Build a fixed-host URL, encoding GitLab project IDs and refs as single parts."""
    if provider == "github":
        path = f"/repos/{quote(repository, safe='/')}"
        ref_endpoint = "commits"
    elif provider == "gitlab":
        path = f"/projects/{quote(repository, safe='')}"
        ref_endpoint = "repository/commits"
    else:
        path = f"/repositories/{quote(repository, safe='/')}"
        ref_endpoint = "commit"
    if ref is not None:
        path += f"/{ref_endpoint}/{quote(ref, safe='')}"
    return f"{_PROVIDER_API_BASE_URLS[provider]}{path}"


def _provider_auth_headers(
    provider: RepositoryProvider, token: str | None
) -> dict[str, str]:
    """Return the provider authorization header used by repository access."""
    if token is None:
        return {}
    if provider == "gitlab":
        # OAuth tokens require Bearer; GitLab access tokens also accept it.
        return {"Authorization": f"Bearer {token}"}
    if provider == "bitbucket":
        # The SDK's clone path uses ``x-token-auth`` for a bare Bitbucket
        # token, while the UI also accepts an explicit ``username:token``.
        # Preserve either spelling as HTTP Basic credentials for the API check.
        credentials = token if ":" in token else f"x-token-auth:{token}"
        encoded = base64.b64encode(credentials.encode()).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}
    return {"Authorization": f"Bearer {token}"}


def _resolve_provider_credential(
    store: FileSecretsStore, credential_names: list[str]
) -> str | None:
    """Read the first nonempty secret in priority order; run outside the event loop.

    Later names are fallbacks for missing values, not retries after API rejection.
    """
    for name in credential_names:
        token = store.get_secret(name)
        if token:
            return token
    return None


@git_router.post(
    "/validate-repository",
    response_model=ValidateRepositoryResponse,
)
async def validate_repository(
    request: Request,
    repository_request: ValidateRepositoryRequest,
) -> ValidateRepositoryResponse:
    """Check provider API access using named secrets or anonymous access.

    Provider I/O has an overall deadline. Response bodies are discarded and
    redirects are not followed, so credentials stay on the configured API host.
    """
    update_last_execution_time()
    token: str | None = None
    if repository_request.credential_names:
        try:
            config = get_config(request)
            # File-backed stores use a synchronous lock; keep both initialization
            # and the credential read off the request event loop.
            store = await asyncio.to_thread(get_secrets_store, config)
            token = await asyncio.to_thread(
                _resolve_provider_credential,
                store,
                repository_request.credential_names,
            )
        except (HTTPException, OSError, RuntimeError, ValueError):
            return ValidateRepositoryResponse(status="unavailable")
        if token is None:
            return ValidateRepositoryResponse(status="missing_credentials")

    try:
        url = _provider_repository_url(
            repository_request.provider,
            repository_request.repository,
            repository_request.ref,
        )
        headers = _provider_auth_headers(repository_request.provider, token)
        # The verdict only needs the status and headers. Streaming avoids
        # buffering provider response bodies that may contain sensitive details.
        async with asyncio.timeout(_PROVIDER_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                timeout=_PROVIDER_TIMEOUT_SECONDS,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream("GET", url, headers=headers) as response:
                    response_status = response.status_code
                    response_headers = response.headers
    except (httpx.TransportError, TimeoutError, UnicodeError):
        return ValidateRepositoryResponse(status="unavailable")

    if 200 <= response_status < 300:
        return ValidateRepositoryResponse(status="accessible")
    if response_status == 401:
        return ValidateRepositoryResponse(status="denied")
    if response_status == 403:
        if (
            response_headers.get("retry-after")
            or response_headers.get("x-ratelimit-remaining") == "0"
        ):
            return ValidateRepositoryResponse(status="unavailable")
        return ValidateRepositoryResponse(status="denied")
    if response_status == 404:
        return ValidateRepositoryResponse(status="not_found")
    return ValidateRepositoryResponse(status="unavailable")


async def _get_git_changes(path: str, ref: str | None) -> list[GitChange]:
    """Internal helper to get git changes for a given path."""
    update_last_execution_time()
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, functools.partial(get_git_changes, Path(path), ref=ref)
        )
    except GitRepositoryError:
        # A non-repo workspace has no git changes to report; respond with an
        # empty list so the Changes tab can render normally instead of 500ing.
        logger.debug("Path %s is not a git repository; returning no changes", path)
        return []


async def _get_git_diff(path: str, ref: str | None) -> GitDiff:
    """Internal helper to get git diff for a given path."""
    update_last_execution_time()
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, functools.partial(get_git_diff, Path(path), ref=ref)
        )
    except GitRepositoryError:
        # Only collapse the not-a-repo case to an empty diff; file-level
        # GitPathError (missing/oversize/outside-repo) stays a 500 so
        # callers can distinguish it from "no changes".
        logger.debug("Path %s is not in a git repository; returning empty diff", path)
        return GitDiff(modified=None, original=None)


async def _get_git_commits(path: str, limit: int) -> GitCommitsPage:
    """Internal helper to list commits for a given repo path."""
    update_last_execution_time()
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, functools.partial(get_git_commits, Path(path), limit=limit)
        )
    except GitRepositoryError:
        # A non-repo workspace has no commits to report; respond with an
        # empty page so the commits section can render its empty state.
        logger.debug("Path %s is not a git repository; returning no commits", path)
        return GitCommitsPage(commits=[], has_more=False)


async def _get_commit_changes(path: str, sha: str) -> list[GitChange]:
    """Internal helper to get the files changed by one commit."""
    update_last_execution_time()
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, functools.partial(get_commit_changes, Path(path), sha)
        )
    except GitRepositoryError:
        logger.debug("Path %s is not a git repository; returning no changes", path)
        return []


async def _get_commit_file_diff(path: str, commit: str) -> GitDiff:
    """Internal helper to get one file's diff as changed by one commit."""
    update_last_execution_time()
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, functools.partial(get_commit_file_diff, Path(path), commit)
        )
    except GitRepositoryError:
        logger.debug("Path %s is not in a git repository; returning empty diff", path)
        return GitDiff(modified=None, original=None)


@git_router.get("/repositories/search", response_model=GitProviderRepositoryPage)
async def git_repositories_search(
    request: Request,
    provider: Annotated[GitProvider, Query(description="Git provider to search")],
    query: Annotated[
        str | None, Query(description="Optional repository name filter")
    ] = None,
    limit: Annotated[
        int, Query(ge=1, le=100, description="Maximum repositories to return")
    ] = 100,
    page_id: Annotated[
        str | None, Query(description="Provider-specific page cursor")
    ] = None,
) -> GitProviderRepositoryPage:
    """List repositories accessible to a configured git provider token."""
    try:
        return await search_provider_repositories(
            get_config(request),
            provider,
            query=query,
            limit=limit,
            page_id=page_id,
        )
    except UnsupportedGitProviderError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except GitProviderAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e


@git_router.get("/changes")
@runtime_git_router.get("/changes")
async def git_changes_query(
    path: str = Query(..., description="The git repository path"),
    ref: str | None = Query(None, description=_REF_QUERY_DESCRIPTION),
) -> list[GitChange]:
    """Get git changes using query parameter (preferred method)."""
    try:
        return await _get_git_changes(path, ref)
    except GitError as e:
        # GitRepositoryError is already handled in the helper (returns []).
        # Any remaining GitError subclass (e.g. GitCommandError) surfaces as
        # 400 so the client can show an actionable error instead of an
        # opaque 500.
        raise HTTPException(status_code=400, detail=str(e))


@git_router.get("/diff")
@runtime_git_router.get("/diff")
async def git_diff_query(
    path: str = Query(..., description="The file path to get diff for"),
    ref: str | None = Query(None, description=_REF_QUERY_DESCRIPTION),
    commit: str | None = Query(
        None, pattern=_SHA_PATTERN, description=_COMMIT_QUERY_DESCRIPTION
    ),
) -> GitDiff:
    """Get git diff using query parameter (preferred method)."""
    if ref is not None and commit is not None:
        raise HTTPException(
            status_code=400, detail="'ref' and 'commit' are mutually exclusive"
        )
    try:
        if commit is not None:
            return await _get_commit_file_diff(path, commit)
        return await _get_git_diff(path, ref)
    except GitError as e:
        # GitRepositoryError is already handled in the helpers (returns an
        # empty diff). Any remaining GitError subclass (e.g. GitCommandError,
        # GitPathError) surfaces as 400 so the client can show an actionable
        # error instead of an opaque 500.
        raise HTTPException(status_code=400, detail=str(e))


@git_router.get("/commits")
@runtime_git_router.get("/commits")
async def git_commits_query(
    path: str = Query(..., description="The git repository path"),
    limit: int = Query(50, ge=1, le=200, description="Maximum commits to return"),
) -> GitCommitsPage:
    """List the repository's most recent commits, newest first."""
    try:
        return await _get_git_commits(path, limit)
    except GitError as e:
        # GitRepositoryError is already handled in the helper (returns an
        # empty page). Any remaining GitError subclass surfaces as 400.
        raise HTTPException(status_code=400, detail=str(e))


@git_router.get("/commits/{sha}/changes")
@runtime_git_router.get("/commits/{sha}/changes")
async def git_commit_changes_query(
    sha: str = PathParam(..., pattern=_SHA_PATTERN, description="Commit SHA"),
    path: str = Query(..., description="The git repository path"),
) -> list[GitChange]:
    """Get the files changed by a single commit (vs its first parent)."""
    try:
        return await _get_commit_changes(path, sha)
    except GitError as e:
        # GitRepositoryError is already handled in the helper (returns []).
        # An unknown/unresolvable sha raises GitCommandError -> 400.
        raise HTTPException(status_code=400, detail=str(e))
