"""Remote git provider requests for Agent Server: repository search, PR status."""

import asyncio
import re
from collections.abc import Coroutine
from typing import Any, Final
from urllib.parse import parse_qs, urlparse

import httpx

from openhands.agent_server.config import Config
from openhands.agent_server.persistence import get_secrets_store
from openhands.sdk.git.models import (
    GitProviderRepository,
    GitProviderRepositoryPage,
    GitPullRequestChecks,
    GitPullRequestState,
    GitPullRequestStatus,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.workspace.repo import PROVIDER_TOKEN_NAMES, GitProvider


logger = get_logger(__name__)

_GITHUB_API_URL: Final[str] = "https://api.github.com"
_GITHUB_USER_REPOS_PATH: Final[str] = "/user/repos"
_GITHUB_ACCEPT_HEADER: Final[str] = "application/vnd.github+json"
_GITHUB_API_VERSION: Final[str] = "2022-11-28"
_GITHUB_REPOSITORY_SORT: Final[str] = "pushed"
_GITHUB_REPOSITORY_AFFILIATION: Final[str] = "owner,collaborator,organization_member"
_GITHUB_REPOSITORY_PAGE_SIZE: Final[int] = 100
_GITHUB_REQUEST_TIMEOUT_SECS: Final[int] = 15
_GITHUB_TOKEN_CANDIDATES: Final[tuple[str, ...]] = (
    PROVIDER_TOKEN_NAMES[GitProvider.GITHUB],
    "github_token",
    "GH_TOKEN",
    "github",
)
_PROVIDER_TOKEN_CANDIDATES: Final[dict[GitProvider, tuple[str, ...]]] = {
    GitProvider.GITHUB: _GITHUB_TOKEN_CANDIDATES,
}
_LINK_PART_RE: Final[re.Pattern[str]] = re.compile(r'<([^>]+)>;\s*rel="([^"]+)"')


class GitProviderError(Exception):
    """Base error for remote git provider requests."""


# Former name, from when repository search was the only provider request.
GitProviderRepositorySearchError = GitProviderError


class UnsupportedGitProviderError(GitProviderError):
    """Raised when a provider is not supported for a request yet."""


class MissingGitProviderTokenError(GitProviderError):
    """Raised when no token is configured for the provider."""


class GitProviderAPIError(GitProviderError):
    """Raised when a provider API request fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


async def search_provider_repositories(
    config: Config,
    provider: GitProvider,
    *,
    query: str | None = None,
    limit: int = 100,
    page_id: str | None = None,
) -> GitProviderRepositoryPage:
    """List repositories the configured provider token can access."""
    if provider != GitProvider.GITHUB:
        raise UnsupportedGitProviderError(
            f"Repository discovery is not supported for provider '{provider.value}'"
        )

    token = _resolve_provider_token(config, provider)
    if token is None:
        return GitProviderRepositoryPage(
            items=[], next_page_id=None, missing_token=True
        )

    return await _search_github_repositories(
        token,
        query=query,
        limit=limit,
        page_id=page_id,
    )


_FAILED_CHECK_CONCLUSIONS: Final[frozenset[str]] = frozenset(
    {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
)
_CHECK_RUNS_PAGE_SIZE: Final[int] = 100
# Pages of check runs read before reporting "pending" instead of a result.
_CHECK_RUNS_MAX_PAGES: Final[int] = 5


async def get_provider_pull_request(
    config: Config,
    provider: GitProvider,
    owner: str,
    repo: str,
    number: int,
) -> GitPullRequestStatus:
    """Read one pull request's state and the combined CI result of its head."""
    if provider != GitProvider.GITHUB:
        raise UnsupportedGitProviderError(
            f"Pull requests are not supported for provider '{provider.value}'"
        )
    token = _resolve_provider_token(config, provider)
    if token is None:
        raise MissingGitProviderTokenError(f"No {provider.value} token is configured")

    base = f"/repos/{owner}/{repo}"
    async with _github_client(token) as client:
        pr = (
            await _github_get(client, f"{base}/pulls/{number}", for_badge=True)
        ).json()
        head_sha = _optional_str((pr.get("head") or {}).get("sha"))
        checks = GitPullRequestChecks.NONE
        if head_sha:
            check_runs, statuses = await _both(
                _read_check_runs(client, f"{base}/commits/{head_sha}/check-runs"),
                _github_get(
                    client, f"{base}/commits/{head_sha}/status", for_badge=True
                ),
            )
            checks = _combine_checks(check_runs, statuses.json().get("statuses") or [])

    if pr.get("merged") or pr.get("merged_at"):
        state = GitPullRequestState.MERGED
    elif pr.get("state") == "closed":
        state = GitPullRequestState.CLOSED
    else:
        state = GitPullRequestState.OPEN
    return GitPullRequestStatus(
        git_provider=provider.value,
        repository=f"{owner}/{repo}",
        number=number,
        state=state,
        draft=bool(pr.get("draft", False)),
        title=str(pr.get("title", "")),
        url=str(pr.get("html_url", "")),
        head_sha=head_sha,
        checks=checks,
    )


async def _both[A, B](
    first: Coroutine[Any, Any, A], second: Coroutine[Any, Any, B]
) -> tuple[A, B]:
    """Run two reads concurrently; if one fails, cancel the other before raising.

    The reads share a client that closes when the caller's ``async with``
    ends, so neither may outlive a failure. Unlike a TaskGroup, the first
    error is raised as itself, not inside an ExceptionGroup.
    """
    first_task = asyncio.ensure_future(first)
    second_task = asyncio.ensure_future(second)
    try:
        return await asyncio.gather(first_task, second_task)
    except BaseException:
        for task in (first_task, second_task):
            task.cancel()
        await asyncio.gather(first_task, second_task, return_exceptions=True)
        raise


async def _read_check_runs(
    client: httpx.AsyncClient, path: str
) -> list[dict[str, object]] | None:
    """All check runs of a commit, or None when there are too many to read."""
    runs: list[dict[str, object]] = []
    for page in range(1, _CHECK_RUNS_MAX_PAGES + 1):
        body = (
            await _github_get(
                client,
                path,
                params={"per_page": _CHECK_RUNS_PAGE_SIZE, "page": page},
                for_badge=True,
            )
        ).json()
        page_runs = body.get("check_runs") or []
        runs.extend(page_runs)
        total = body.get("total_count")
        if not isinstance(total, int) or len(runs) >= total or not page_runs:
            return runs
    return None


def _combine_checks(
    check_runs: list[dict[str, object]] | None, statuses: list[dict[str, object]]
) -> GitPullRequestChecks:
    """Fold check runs and commit statuses into one badge: failure wins.

    ``check_runs`` is None when there were more runs than were read; then a
    result short of failure is reported as pending, never as success.
    """
    unread = check_runs is None
    check_runs = check_runs or []
    if not check_runs and not statuses and not unread:
        return GitPullRequestChecks.NONE
    pending = unread
    for run in check_runs:
        if run.get("status") != "completed":
            pending = True
        elif run.get("conclusion") in _FAILED_CHECK_CONCLUSIONS:
            return GitPullRequestChecks.FAILURE
    for item in statuses:
        state = item.get("state")
        if state in {"failure", "error"}:
            return GitPullRequestChecks.FAILURE
        if state == "pending":
            pending = True
    return GitPullRequestChecks.PENDING if pending else GitPullRequestChecks.SUCCESS


def _github_client(token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_GITHUB_API_URL,
        headers={
            "Accept": _GITHUB_ACCEPT_HEADER,
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        },
        timeout=_GITHUB_REQUEST_TIMEOUT_SECS,
    )


async def _github_get(
    client: httpx.AsyncClient,
    path: str,
    params: dict[str, str | int] | None = None,
    *,
    for_badge: bool = False,
) -> httpx.Response:
    """GET from GitHub, turning failures into GitProviderAPIError.

    With ``for_badge``, GitHub's 404 stays 404, its rate limit stays 429, and a
    rejected token becomes 502 (a 401 from the agent server would read as the
    caller's own session key being wrong). Otherwise the search mapping holds.
    """
    try:
        response = await client.get(path, params=params)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        logger.warning("GitHub request failed: status=%s", status_code)
        if for_badge:
            raise _badge_api_error(e.response) from e
        raise GitProviderAPIError(
            "GitHub request failed",
            status_code=_map_github_status_code(status_code),
        ) from e
    except httpx.TimeoutException as e:
        logger.warning("GitHub request timed out")
        raise GitProviderAPIError("GitHub request timed out", status_code=504) from e
    except httpx.HTTPError as e:
        logger.warning("GitHub request failed: %s", type(e).__name__)
        raise GitProviderAPIError("GitHub request failed") from e
    return response


def _badge_api_error(response: httpx.Response) -> GitProviderAPIError:
    status_code = response.status_code
    if status_code == 404:
        return GitProviderAPIError("GitHub did not find it", status_code=404)
    rate_limited = status_code == 429 or (
        status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0"
    )
    if rate_limited:
        return GitProviderAPIError(
            "GitHub rate limit reached",
            status_code=429,
            retry_after=response.headers.get("retry-after"),
        )
    if status_code in {401, 403}:
        return GitProviderAPIError(
            "GitHub rejected the configured token", status_code=502
        )
    return GitProviderAPIError(
        "GitHub request failed", status_code=_map_github_status_code(status_code)
    )


def _resolve_provider_token(config: Config, provider: GitProvider) -> str | None:
    store = get_secrets_store(config)
    for name in _PROVIDER_TOKEN_CANDIDATES.get(provider, ()):  # pragma: no branch
        if value := store.get_secret(name):
            return value
    return None


async def _search_github_repositories(
    token: str,
    *,
    query: str | None,
    limit: int,
    page_id: str | None,
) -> GitProviderRepositoryPage:
    page, offset = _parse_github_page_id(page_id)
    normalized_query = query.casefold() if query else None
    items: list[GitProviderRepository] = []
    next_page_id: str | None = None
    async with _github_client(token) as client:
        while len(items) < limit:
            response = await _get_github_repository_page(client, page)
            page_items = [_github_repository_to_model(item) for item in response.json()]
            if normalized_query:
                page_items = [
                    item
                    for item in page_items
                    if normalized_query in item.full_name.casefold()
                ]
            if offset:
                page_items = page_items[offset:]

            remaining = limit - len(items)
            items.extend(page_items[:remaining])
            next_page_id = _next_github_page_id(response.headers.get("link"))
            if len(page_items) > remaining:
                return GitProviderRepositoryPage(
                    items=items,
                    next_page_id=f"{page}:{offset + remaining}",
                    missing_token=False,
                )
            if normalized_query is None:
                break

            if next_page_id is None:
                break
            page, offset = _parse_github_page_id(next_page_id)

    return GitProviderRepositoryPage(
        items=items,
        next_page_id=next_page_id,
        missing_token=False,
    )


async def _get_github_repository_page(
    client: httpx.AsyncClient, page: int
) -> httpx.Response:
    params: dict[str, str | int] = {
        "per_page": _GITHUB_REPOSITORY_PAGE_SIZE,
        "page": page,
        "sort": _GITHUB_REPOSITORY_SORT,
        "affiliation": _GITHUB_REPOSITORY_AFFILIATION,
    }
    return await _github_get(client, _GITHUB_USER_REPOS_PATH, params)


def _parse_github_page_id(page_id: str | None) -> tuple[int, int]:
    if page_id is None:
        return 1, 0
    try:
        raw_page, _, raw_offset = page_id.partition(":")
        page = int(raw_page)
        offset = int(raw_offset) if raw_offset else 0
    except ValueError as e:
        raise GitProviderAPIError("Invalid repository page_id", status_code=400) from e
    if page < 1 or offset < 0:
        raise GitProviderAPIError("Invalid repository page_id", status_code=400)
    return page, offset


def _map_github_status_code(status_code: int) -> int:
    if status_code in {401, 403}:
        return status_code
    if status_code >= 500:
        return 502
    return 400


def _next_github_page_id(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for url, rel in _LINK_PART_RE.findall(link_header):
        if rel != "next":
            continue
        query = parse_qs(urlparse(url).query)
        page = query.get("page", [None])[0]
        return page or None
    return None


def _github_repository_to_model(item: dict[str, object]) -> GitProviderRepository:
    return GitProviderRepository(
        id=str(item.get("id", "")),
        full_name=str(item.get("full_name", "")),
        git_provider=GitProvider.GITHUB.value,
        is_public=not bool(item.get("private", False)),
        stargazers_count=_optional_int(item.get("stargazers_count")),
        pushed_at=_optional_str(item.get("pushed_at")),
        main_branch=_optional_str(item.get("default_branch")),
    )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None
