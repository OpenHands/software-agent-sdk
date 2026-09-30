"""Tests for git provider repository discovery."""

import pytest

from openhands.agent_server.config import Config
from openhands.agent_server.git_provider_service import (
    GitProviderAPIError,
    search_provider_repositories,
)
from openhands.sdk.workspace.repo import GitProvider


@pytest.mark.asyncio
async def test_search_provider_repositories_reports_missing_token(monkeypatch):
    monkeypatch.setattr(
        "openhands.agent_server.git_provider_service._resolve_provider_token",
        lambda _config, _provider: None,
    )

    result = await search_provider_repositories(Config(), GitProvider.GITHUB)

    assert result.items == []
    assert result.next_page_id is None
    assert result.missing_token is True


@pytest.mark.asyncio
async def test_search_provider_repositories_maps_github_repositories(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "openhands.agent_server.git_provider_service._resolve_provider_token",
        lambda _config, _provider: "github-token",
    )

    class FakeResponse:
        def __init__(self) -> None:
            self.headers = {
                "link": '<https://api.github.com/user/repos?page=3>; rel="next"'
            }

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return [
                {
                    "id": 123,
                    "full_name": "OpenHands/software-agent-sdk",
                    "private": False,
                    "stargazers_count": 7,
                    "pushed_at": "2026-09-29T12:00:00Z",
                    "default_branch": "main",
                },
                {
                    "id": 456,
                    "full_name": "OpenHands/other",
                    "private": True,
                    "stargazers_count": 1,
                    "pushed_at": None,
                    "default_branch": "trunk",
                },
            ]

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["init"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, path, params):
            captured["path"] = path
            captured["params"] = params
            return FakeResponse()

    monkeypatch.setattr(
        "openhands.agent_server.git_provider_service.httpx.AsyncClient",
        FakeAsyncClient,
    )

    result = await search_provider_repositories(
        Config(),
        GitProvider.GITHUB,
        query="software",
        limit=30,
        page_id="2",
    )

    assert captured["path"] == "/user/repos"
    assert captured["params"] == {
        "per_page": 30,
        "page": 2,
        "sort": "pushed",
        "affiliation": "owner,collaborator,organization_member",
    }
    assert result.next_page_id == "3"
    assert result.missing_token is False
    assert len(result.items) == 1
    repo = result.items[0]
    assert repo.id == "123"
    assert repo.full_name == "OpenHands/software-agent-sdk"
    assert repo.git_provider == "github"
    assert repo.is_public is True
    assert repo.stargazers_count == 7
    assert repo.pushed_at == "2026-09-29T12:00:00Z"
    assert repo.main_branch == "main"


@pytest.mark.asyncio
async def test_search_provider_repositories_rejects_invalid_page_id(monkeypatch):
    monkeypatch.setattr(
        "openhands.agent_server.git_provider_service._resolve_provider_token",
        lambda _config, _provider: "github-token",
    )

    with pytest.raises(GitProviderAPIError):
        await search_provider_repositories(
            Config(), GitProvider.GITHUB, page_id="not-a-page"
        )
