"""Remote git provider repository discovery for Agent Server."""

import re
from typing import Final, Literal
from urllib.parse import parse_qs, urlparse

import httpx

from openhands.agent_server.config import Config
from openhands.agent_server.models import GitPullRequestStatus
from openhands.agent_server.persistence import get_secrets_store
from openhands.sdk.git.models import GitProviderRepository, GitProviderRepositoryPage
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


class GitProviderRepositorySearchError(Exception):
    """Base error for remote provider repository discovery."""


class UnsupportedGitProviderError(GitProviderRepositorySearchError):
    """Raised when repository discovery does not support a provider yet."""


class MissingGitProviderTokenError(GitProviderRepositorySearchError):
    """Raised when no token is configured for the provider."""


class GitProviderAPIError(GitProviderRepositorySearchError):
    """Raised when a provider API request fails."""

    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


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

    headers = {
        "Accept": _GITHUB_ACCEPT_HEADER,
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
    }
    base = f"/repos/{owner}/{repo}"
    async with httpx.AsyncClient(
        base_url=_GITHUB_API_URL,
        headers=headers,
        timeout=_GITHUB_REQUEST_TIMEOUT_SECS,
    ) as client:
        pr = (await _github_get(client, f"{base}/pulls/{number}")).json()
        head_sha = str((pr.get("head") or {}).get("sha", ""))
        checks = "none"
        if head_sha:
            runs = (
                await _github_get(
                    client,
                    f"{base}/commits/{head_sha}/check-runs",
                    params={"per_page": _CHECK_RUNS_PAGE_SIZE},
                )
            ).json()
            status = (
                await _github_get(client, f"{base}/commits/{head_sha}/status")
            ).json()
            checks = _combine_checks(
                runs.get("check_runs") or [], status.get("statuses") or []
            )

    if pr.get("merged") or pr.get("merged_at"):
        state = "merged"
    elif pr.get("state") == "closed":
        state = "closed"
    else:
        state = "open"
    return GitPullRequestStatus(
        state=state,
        draft=bool(pr.get("draft", False)),
        title=str(pr.get("title", "")),
        url=str(pr.get("html_url", "")),
        checks=checks,
    )


def _combine_checks(
    check_runs: list[dict[str, object]], statuses: list[dict[str, object]]
) -> Literal["success", "failure", "pending", "none"]:
    """Fold check runs and commit statuses into one badge: failure wins."""
    if not check_runs and not statuses:
        return "none"
    pending = False
    for run in check_runs:
        if run.get("status") != "completed":
            pending = True
        elif run.get("conclusion") in _FAILED_CHECK_CONCLUSIONS:
            return "failure"
    for item in statuses:
        state = item.get("state")
        if state in {"failure", "error"}:
            return "failure"
        if state == "pending":
            pending = True
    return "pending" if pending else "success"


async def _github_get(
    client: httpx.AsyncClient,
    path: str,
    params: dict[str, str | int] | None = None,
) -> httpx.Response:
    try:
        response = await client.get(path, params=params)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        logger.warning("GitHub request failed: status=%s", status_code)
        mapped = 404 if status_code == 404 else _map_github_status_code(status_code)
        raise GitProviderAPIError("GitHub request failed", status_code=mapped) from e
    except httpx.TimeoutException as e:
        logger.warning("GitHub request timed out")
        raise GitProviderAPIError("GitHub request timed out", status_code=504) from e
    except httpx.HTTPError as e:
        logger.warning("GitHub request failed: %s", type(e).__name__)
        raise GitProviderAPIError("GitHub request failed") from e
    return response


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
    headers = {
        "Accept": _GITHUB_ACCEPT_HEADER,
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
    }

    items: list[GitProviderRepository] = []
    next_page_id: str | None = None
    async with httpx.AsyncClient(
        base_url=_GITHUB_API_URL,
        headers=headers,
        timeout=_GITHUB_REQUEST_TIMEOUT_SECS,
    ) as client:
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
    try:
        response = await client.get(_GITHUB_USER_REPOS_PATH, params=params)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        logger.warning("GitHub repository search failed: status=%s", status_code)
        raise GitProviderAPIError(
            "GitHub repository search failed",
            status_code=_map_github_status_code(status_code),
        ) from e
    except httpx.TimeoutException as e:
        logger.warning("GitHub repository search timed out")
        raise GitProviderAPIError(
            "GitHub repository search timed out", status_code=504
        ) from e
    except httpx.HTTPError as e:
        logger.warning("GitHub repository search failed: %s", type(e).__name__)
        raise GitProviderAPIError("GitHub repository search failed") from e
    return response


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
