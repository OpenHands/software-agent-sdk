"""Tests for git provider repository discovery."""

from typing import Any

import httpx
import pytest

from openhands.agent_server.config import Config
from openhands.agent_server.git_provider_service import (
    GitProviderAPIError,
    search_provider_repositories,
)
from openhands.sdk.workspace.repo import GitProvider


class FakeResponse:
    def __init__(
        self,
        items: list[dict[str, object]] | None = None,
        *,
        next_page: int | None = None,
        status_code: int = 200,
    ) -> None:
        self._items = items or []
        self.status_code = status_code
        self.headers = {}
        if next_page is not None:
            self.headers["link"] = (
                f'<https://api.github.com/user/repos?page={next_page}>; rel="next"'
            )

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://api.github.com/user/repos")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                "GitHub error", request=request, response=response
            )

    def json(self) -> list[dict[str, object]]:
        return self._items


class FakeAsyncClient:
    def __init__(self, responses: list[FakeResponse], captured: dict[str, Any]):
        self._responses = responses
        self._captured = captured
        captured["init_count"] = captured.get("init_count", 0) + 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, path, params):
        self._captured.setdefault("calls", []).append((path, params))
        return self._responses.pop(0)


def _repo(full_name: str, repo_id: int) -> dict[str, object]:
    return {
        "id": repo_id,
        "full_name": full_name,
        "private": False,
        "stargazers_count": repo_id,
        "pushed_at": "2026-09-29T12:00:00Z",
        "default_branch": "main",
    }


def _patch_provider_token(monkeypatch, token: str | None = "github-token") -> None:
    monkeypatch.setattr(
        "openhands.agent_server.git_provider_service._resolve_provider_token",
        lambda _config, _provider: token,
    )


def _patch_github_client(monkeypatch, responses: list[FakeResponse]):
    captured: dict[str, Any] = {}

    class BoundFakeAsyncClient(FakeAsyncClient):
        def __init__(self, **_kwargs):
            super().__init__(responses, captured)
            captured["init"] = _kwargs

    monkeypatch.setattr(
        "openhands.agent_server.git_provider_service.httpx.AsyncClient",
        BoundFakeAsyncClient,
    )
    return captured


@pytest.mark.asyncio
async def test_search_provider_repositories_reports_missing_token(monkeypatch):
    _patch_provider_token(monkeypatch, None)

    result = await search_provider_repositories(Config(), GitProvider.GITHUB)

    assert result.items == []
    assert result.next_page_id is None
    assert result.missing_token is True


@pytest.mark.asyncio
async def test_search_provider_repositories_maps_github_repositories(monkeypatch):
    _patch_provider_token(monkeypatch)
    captured = _patch_github_client(
        monkeypatch,
        [
            FakeResponse(
                [
                    _repo("OpenHands/software-agent-sdk", 123),
                    {
                        **_repo("OpenHands/other", 456),
                        "private": True,
                        "default_branch": "trunk",
                    },
                ],
                next_page=3,
            )
        ],
    )

    result = await search_provider_repositories(
        Config(),
        GitProvider.GITHUB,
        limit=30,
        page_id="2",
    )

    assert captured["calls"] == [
        (
            "/user/repos",
            {
                "per_page": 30,
                "page": 2,
                "sort": "pushed",
                "affiliation": "owner,collaborator,organization_member",
            },
        )
    ]
    assert result.next_page_id == "3"
    assert result.missing_token is False
    assert len(result.items) == 2
    repo = result.items[0]
    assert repo.id == "123"
    assert repo.full_name == "OpenHands/software-agent-sdk"
    assert repo.git_provider == "github"
    assert repo.is_public is True
    assert repo.stargazers_count == 123
    assert repo.pushed_at == "2026-09-29T12:00:00Z"
    assert repo.main_branch == "main"


@pytest.mark.asyncio
async def test_search_provider_repositories_filters_across_pages(monkeypatch):
    _patch_provider_token(monkeypatch)
    captured = _patch_github_client(
        monkeypatch,
        [
            FakeResponse([_repo("OpenHands/first-page", 1)], next_page=2),
            FakeResponse(
                [
                    _repo("OpenHands/software-agent-sdk", 2),
                    _repo("OpenHands/software-agents", 3),
                    _repo("OpenHands/another", 4),
                ]
            ),
        ],
    )

    result = await search_provider_repositories(
        Config(), GitProvider.GITHUB, query="software", limit=2
    )

    assert captured["calls"] == [
        (
            "/user/repos",
            {
                "per_page": 2,
                "page": 1,
                "sort": "pushed",
                "affiliation": "owner,collaborator,organization_member",
            },
        ),
        (
            "/user/repos",
            {
                "per_page": 2,
                "page": 2,
                "sort": "pushed",
                "affiliation": "owner,collaborator,organization_member",
            },
        ),
    ]
    assert [item.full_name for item in result.items] == [
        "OpenHands/software-agent-sdk",
        "OpenHands/software-agents",
    ]
    assert result.next_page_id is None


@pytest.mark.asyncio
async def test_search_provider_repositories_continues_from_offset_cursor(monkeypatch):
    _patch_provider_token(monkeypatch)
    _patch_github_client(
        monkeypatch,
        [
            FakeResponse(
                [
                    _repo("OpenHands/software-agent-sdk", 1),
                    _repo("OpenHands/software-agents", 2),
                ],
                next_page=3,
            )
        ],
    )

    result = await search_provider_repositories(
        Config(), GitProvider.GITHUB, query="software", limit=1, page_id="2:1"
    )

    assert [item.full_name for item in result.items] == ["OpenHands/software-agents"]
    assert result.next_page_id == "3"


@pytest.mark.asyncio
async def test_search_provider_repositories_rejects_invalid_page_id(monkeypatch):
    _patch_provider_token(monkeypatch)

    with pytest.raises(GitProviderAPIError) as exc_info:
        await search_provider_repositories(
            Config(), GitProvider.GITHUB, page_id="not-a-page"
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_status", "expected_status"),
    [(401, 401), (403, 403), (404, 400), (500, 502)],
)
async def test_search_provider_repositories_maps_provider_status(
    monkeypatch, provider_status, expected_status
):
    _patch_provider_token(monkeypatch)
    _patch_github_client(monkeypatch, [FakeResponse(status_code=provider_status)])

    with pytest.raises(GitProviderAPIError) as exc_info:
        await search_provider_repositories(Config(), GitProvider.GITHUB)

    assert exc_info.value.status_code == expected_status


@pytest.mark.asyncio
async def test_search_provider_repositories_maps_timeout(monkeypatch):
    _patch_provider_token(monkeypatch)

    class TimeoutAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, _path, params):
            raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(
        "openhands.agent_server.git_provider_service.httpx.AsyncClient",
        TimeoutAsyncClient,
    )

    with pytest.raises(GitProviderAPIError) as exc_info:
        await search_provider_repositories(Config(), GitProvider.GITHUB)

    assert exc_info.value.status_code == 504
