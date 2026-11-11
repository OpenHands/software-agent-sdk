"""Deployment switches for the default public extensions source."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server import plugins_service, skills_service
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.skills_service import load_all_skills
from openhands.sdk import AgentContext
from openhands.sdk.marketplace import MarketplaceRegistration
from openhands.sdk.skills.skill import (
    PUBLIC_PLUGIN_CATALOG_ENABLED_ENV,
    PUBLIC_SKILLS_ENABLED_ENV,
    load_public_skills,
    public_plugin_catalog_enabled,
    public_skills_enabled,
)
from tests.sdk.conversation.test_local_conversation_plugins import (
    create_test_marketplace,
    create_test_marketplace_with_standalone_skills,
)


def _standalone(tmp_path):
    return create_test_marketplace_with_standalone_skills(
        tmp_path / "org", skills=["github", "org-unique"]
    )


def _plugin(tmp_path):
    skills = [{"name": "github", "content": "PLUGIN-GITHUB"}]
    return create_test_marketplace(
        tmp_path / "org", plugins=[{"name": "org-tools", "skills": skills}]
    )


def _skills(marketplace, **kwargs) -> dict[str, str]:
    registration = MarketplaceRegistration(
        name="org", source=str(marketplace), auto_load=True
    )
    result = load_all_skills(
        load_user=False,
        load_project=False,
        load_org=False,
        registered_marketplaces=[registration],
        **kwargs,
    )
    return {skill.name: skill.content.strip() for skill in result.skills}


def _client() -> TestClient:
    return TestClient(create_app(Config(session_api_keys=[])))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        ("", True),
        ("true", True),
        ("1", True),
        ("false", False),
        ("0", False),
        ("off", False),
        ("enabled", True),
        ("tru", True),
    ],
)
def test_switch_values(monkeypatch, value, expected):
    for env in (PUBLIC_SKILLS_ENABLED_ENV, PUBLIC_PLUGIN_CATALOG_ENABLED_ENV):
        if value is None:
            monkeypatch.delenv(env, raising=False)
        else:
            monkeypatch.setenv(env, value)
    assert public_skills_enabled() is expected
    assert public_plugin_catalog_enabled() is expected


def test_unrecognised_value_is_logged(monkeypatch):
    monkeypatch.setenv(PUBLIC_SKILLS_ENABLED_ENV, "flase")
    with patch("openhands.sdk.skills.skill.logger.warning") as warning:
        assert public_skills_enabled() is True
    assert warning.call_args.args[1] == PUBLIC_SKILLS_ENABLED_ENV


class TestPublicSkillsSwitch:
    def test_disabled_skips_cold_fetch(self, monkeypatch):
        monkeypatch.setenv(PUBLIC_SKILLS_ENABLED_ENV, "false")
        with patch("openhands.sdk.skills.skill.update_skills_repository") as update:
            assert load_public_skills() == []
        update.assert_not_called()

    @pytest.mark.usefixtures("public_repo")
    def test_disabled_ignores_warm_cache(self, monkeypatch):
        assert {s.name for s in load_public_skills()} == {"github", "public-only"}
        monkeypatch.setenv(PUBLIC_SKILLS_ENABLED_ENV, "false")
        assert load_public_skills() == []

    def test_custom_repository_is_not_governed(self, monkeypatch):
        monkeypatch.setenv(PUBLIC_SKILLS_ENABLED_ENV, "false")
        with patch(
            "openhands.sdk.skills.skill.update_skills_repository", return_value=None
        ) as update:
            load_public_skills(repo_url="https://example.invalid/custom")
        update.assert_called_once()

    @pytest.mark.usefixtures("public_repo")
    def test_caller_cannot_reenable_through_agent_context(self, monkeypatch):
        monkeypatch.setenv(PUBLIC_SKILLS_ENABLED_ENV, "0")
        assert AgentContext(load_public_skills=True).skills == []

    @pytest.mark.usefixtures("public_repo")
    @pytest.mark.parametrize("factory", [_standalone, _plugin])
    def test_disabled_keeps_organization_github(self, monkeypatch, tmp_path, factory):
        monkeypatch.setenv(PUBLIC_SKILLS_ENABLED_ENV, "false")
        skills = _skills(factory(tmp_path), load_public=True)
        assert "github" in skills and skills["github"] != "PUBLIC-github"
        assert "public-only" not in skills


@pytest.mark.usefixtures("public_repo")
class TestMarketplaceLoadingSwitch:
    @pytest.mark.parametrize(
        ("load_public", "load_registered", "public", "org"),
        [
            (True, None, True, True),
            (False, None, False, False),  # legacy callers keep the old coupling
            (False, True, False, True),
            (True, False, True, False),
        ],
    )
    def test_combinations(self, tmp_path, load_public, load_registered, public, org):
        skills = _skills(
            _standalone(tmp_path),
            load_public=load_public,
            load_registered_marketplaces=load_registered,
        )
        assert ("public-only" in skills) is public
        assert ("org-unique" in skills) is org

    @pytest.mark.parametrize("public_env", ["true", "false"])
    def test_route_accepts_independent_flag(self, tmp_path, monkeypatch, public_env):
        monkeypatch.setenv(PUBLIC_SKILLS_ENABLED_ENV, public_env)
        org = _standalone(tmp_path)
        response = _client().post(
            "/api/skills",
            json={
                "load_public": False,
                "load_user": False,
                "load_project": False,
                "load_org": False,
                "load_registered_marketplaces": True,
                "registered_marketplaces": [
                    {"name": "org", "source": str(org), "auto_load": True}
                ],
            },
        )
        assert response.status_code == 200
        names = {skill["name"] for skill in response.json()["skills"]}
        assert names == {"github", "org-unique"}


class TestPublicCatalogSwitch:
    @pytest.fixture(autouse=True)
    def _clear_catalog_caches(self, monkeypatch):
        monkeypatch.setattr(skills_service, "_catalog_cache", None)
        monkeypatch.setattr(plugins_service, "_plugin_catalog_cache", None)

    def test_disabled_never_fetches_cold(self, monkeypatch):
        monkeypatch.setenv(PUBLIC_PLUGIN_CATALOG_ENABLED_ENV, "false")
        with (
            patch.object(plugins_service, "_fetch_plugin_catalog_entries") as plugins,
            patch.object(skills_service, "_fetch_catalog_entries") as skills,
        ):
            assert plugins_service.service_get_plugins_marketplace_catalog() == []
            assert skills_service.service_get_marketplace_catalog() == []
        plugins.assert_not_called()
        skills.assert_not_called()

    def test_disabled_does_not_serve_warm_cache(self, monkeypatch, tmp_path):
        entry = plugins_service.MarketplacePluginInfo(
            name="public-plugin", description=None, source="github:o/r", installed=False
        )
        with patch.object(
            plugins_service, "_fetch_plugin_catalog_entries", return_value=[entry]
        ):
            warm = plugins_service.service_get_plugins_marketplace_catalog(
                installed_dir=tmp_path
            )
        assert [p.name for p in warm] == ["public-plugin"]
        monkeypatch.setattr(
            skills_service, "_catalog_cache", (float("inf"), [("s", None, "x")])
        )
        monkeypatch.setenv(PUBLIC_PLUGIN_CATALOG_ENABLED_ENV, "false")
        assert plugins_service.service_get_plugins_marketplace_catalog() == []
        assert skills_service.service_get_marketplace_catalog() == []

    @pytest.mark.parametrize(
        ("path", "key"),
        [
            ("/api/plugins/marketplace", "plugins"),
            ("/api/skills/marketplace", "skills"),
        ],
    )
    def test_routes_report_disabled_state(self, monkeypatch, path, key):
        monkeypatch.setenv(PUBLIC_PLUGIN_CATALOG_ENABLED_ENV, "false")
        body = _client().get(path).json()
        assert body == {key: [], "disabled": True}

    def test_sync_refused_when_both_disabled(self, monkeypatch):
        monkeypatch.setenv(PUBLIC_SKILLS_ENABLED_ENV, "false")
        monkeypatch.setenv(PUBLIC_PLUGIN_CATALOG_ENABLED_ENV, "false")
        with patch(
            "openhands.agent_server.skills_router.sync_public_skills",
            return_value=(True, "ok"),
        ) as sync:
            body = _client().post("/api/skills/sync").json()
        assert body == {
            "status": "error",
            "message": "Public extensions are disabled by instance policy",
        }
        sync.assert_not_called()

    @pytest.mark.parametrize(
        ("skills_env", "catalog_env"), [("true", "false"), ("false", "true")]
    )
    def test_sync_allowed_when_either_enabled(
        self, monkeypatch, skills_env, catalog_env
    ):
        monkeypatch.setenv(PUBLIC_SKILLS_ENABLED_ENV, skills_env)
        monkeypatch.setenv(PUBLIC_PLUGIN_CATALOG_ENABLED_ENV, catalog_env)
        with patch(
            "openhands.agent_server.skills_router.sync_public_skills",
            return_value=(True, "ok"),
        ) as sync:
            body = _client().post("/api/skills/sync").json()
        assert body == {"status": "success", "message": "ok"}
        sync.assert_called_once()
