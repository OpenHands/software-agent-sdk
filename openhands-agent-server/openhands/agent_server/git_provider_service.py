"""Remote git provider repository discovery for Agent Server."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import httpx

from openhands.agent_server.config import Config
from openhands.agent_server.persistence import get_secrets_store
from openhands.sdk.git.models import GitProviderRepository, GitProviderRepositoryPage
from openhands.sdk.logger import get_logger
from openhands.sdk.workspace.repo import PROVIDER_TOKEN_NAMES, GitProvider


logger = get_logger(__name__)

_GITHUB_API_URL = "https://api.github.com"
_GITHUB_TOKEN_CANDIDATES = (
    PROVIDER_TOKEN_NAMES[GitProvider.GITHUB],
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "github",
)
_PROVIDER_TOKEN_CANDIDATES: dict[GitProvider, tuple[str, ...]] = {
    GitProvider.GITHUB: _GITHUB_TOKEN_CANDIDATES,
}
_LINK_PART_RE = re.compile(r'<([^>]+)>;\s*rel="([^"]+)"')


class GitProviderRepositorySearchError(Exception):
    """Base error for remote provider repository discovery."""


class UnsupportedGitProviderError(GitProviderRepositorySearchError):
    """Raised when repository discovery does not support a provider yet."""


class GitProviderAPIError(GitProviderRepositorySearchError):
    """Raised when a provider API request fails."""


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


def _resolve_provider_token(config: Config, provider: GitProvider) -> str | None:
    store = get_secrets_store(config)
    for name in _PROVIDER_TOKEN_CANDIDATES.get(provider, ()):  # pragma: no branch
        value = store.get_secret(name)
        if value:
            return value
    return None


async def _search_github_repositories(
    token: str,
    *,
    query: str | None,
    limit: int,
    page_id: str | None,
) -> GitProviderRepositoryPage:
    page = _parse_github_page_id(page_id)
    params: dict[str, str | int] = {
        "per_page": limit,
        "page": page,
        "sort": "pushed",
        "affiliation": "owner,collaborator,organization_member",
    }
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        async with httpx.AsyncClient(
            base_url=_GITHUB_API_URL,
            headers=headers,
            timeout=15,
        ) as client:
            response = await client.get("/user/repos", params=params)
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        logger.warning("GitHub repository search failed: status=%s", status_code)
        raise GitProviderAPIError("GitHub repository search failed") from e
    except httpx.HTTPError as e:
        logger.warning("GitHub repository search failed: %s", type(e).__name__)
        raise GitProviderAPIError("GitHub repository search failed") from e

    items = [_github_repository_to_model(item) for item in response.json()]
    if query:
        normalized_query = query.casefold()
        items = [
            item for item in items if normalized_query in item.full_name.casefold()
        ]

    return GitProviderRepositoryPage(
        items=items,
        next_page_id=_next_github_page_id(response.headers.get("link")),
        missing_token=False,
    )


def _parse_github_page_id(page_id: str | None) -> int:
    if page_id is None:
        return 1
    try:
        page = int(page_id)
    except ValueError as e:
        raise GitProviderAPIError("Invalid repository page_id") from e
    if page < 1:
        raise GitProviderAPIError("Invalid repository page_id")
    return page


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
