"""Instance policy for the default public extensions source (PRD-198)."""

from pathlib import Path
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
from tests.public_extension_fixtures import (
    ORG_GITHUB,
    PLUGIN_GITHUB,
    PUBLIC_GITHUB,
    make_plugin_marketplace,
    make_standalone_marketplace,
    public_repo,  # noqa: F401
)


def _registration(path: Path, **kwargs) -> MarketplaceRegistration:
    return MarketplaceRegistration(
        name=path.name, source=str(path), auto_load=True, **kwargs
    )


def _by_name(result) -> dict[str, str]:
    return {skill.name: skill.content.strip() for skill in result.skills}


def _load(registrations, **kwargs):
    kwargs.setdefault("load_user", False)
    return load_all_skills(
        load_project=False,
        load_org=False,
        registered_marketplaces=registrations,
        **kwargs,
    )


def _client() -> TestClient:
    return TestClient(create_app(Config(session_api_keys=[])))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        ("", True),
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("0", False),
        ("false", False),
        ("off", False),
    ],
)
def test_policy_flags(monkeypatch, value, expected):
    for env in (PUBLIC_SKILLS_ENABLED_ENV, PUBLIC_PLUGIN_CATALOG_ENABLED_ENV):
        if value is None:
            monkeypatch.delenv(env, raising=False)
        else:
            monkeypatch.setenv(env, value)
    assert public_skills_enabled() is expected
    assert public_plugin_catalog_enabled() is expected


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
    def test_caller_cannot_reenable_through_load_public(self, monkeypatch, tmp_path):
        monkeypatch.setenv(PUBLIC_SKILLS_ENABLED_ENV, "false")
        org = make_standalone_marketplace(tmp_path / "org")
        skills = _by_name(_load([_registration(org)], load_public=True))
        assert skills["github"] == ORG_GITHUB
        assert "public-only" not in skills
        assert "org-unique" in skills


@pytest.mark.usefixtures("public_repo")
class TestDiscoveryPrecedence:
    @pytest.mark.parametrize(
        ("factory", "org_marker"),
        [
            (make_standalone_marketplace, ORG_GITHUB),
            (make_plugin_marketplace, PLUGIN_GITHUB),
        ],
    )
    @pytest.mark.parametrize(
        ("precedence", "winner"),
        [(None, "public"), ("public", "public"), ("marketplace", "org")],
    )
    def test_same_name_github(self, tmp_path, factory, org_marker, precedence, winner):
        org = factory(tmp_path / "org")
        skills = _by_name(
            _load([_registration(org, public_skill_precedence=precedence)])
        )
        assert skills["github"] == (PUBLIC_GITHUB if winner == "public" else org_marker)
        assert "public-only" in skills

    def test_unrelated_and_plugin_skills_survive_losing_github(self, tmp_path):
        standalone = make_standalone_marketplace(tmp_path / "org-a")
        plugins = make_plugin_marketplace(tmp_path / "org-b")
        skills = _by_name(
            _load(
                [
                    _registration(standalone, public_skill_precedence="public"),
                    _registration(plugins, public_skill_precedence="public"),
                ]
            )
        )
        assert skills["github"] == PUBLIC_GITHUB
        assert {"org-unique", "plugin-other"} <= skills.keys()

    def test_user_skill_still_wins_over_preferred_marketplace(self, tmp_path):
        org = make_standalone_marketplace(tmp_path / "org")
        user_root = tmp_path / "user-skills"
        (user_root / "github").mkdir(parents=True)
        (user_root / "github" / "SKILL.md").write_text(
            "---\nname: github\ndescription: user\n---\nUSER-GITHUB\n"
        )
        with patch("openhands.sdk.skills.skill.USER_SKILLS_DIRS", [user_root]):
            skills = _by_name(
                _load(
                    [_registration(org, public_skill_precedence="marketplace")],
                    load_user=True,
                )
            )
        assert skills["github"] == "USER-GITHUB"

    def test_organizations_are_independent(self, tmp_path):
        org_a = make_standalone_marketplace(tmp_path / "org-a", unique="a-only")
        org_b = make_standalone_marketplace(tmp_path / "org-b", unique="b-only")
        a = _by_name(
            _load([_registration(org_a, public_skill_precedence="marketplace")])
        )
        b = _by_name(_load([_registration(org_b, public_skill_precedence="public")]))
        assert a["github"] == ORG_GITHUB and "b-only" not in a
        assert b["github"] == PUBLIC_GITHUB and "a-only" not in b

    def test_auto_load_false_loads_nothing_from_marketplace(self, tmp_path):
        org = make_standalone_marketplace(tmp_path / "org")
        registration = MarketplaceRegistration(
            name="org", source=str(org), public_skill_precedence="marketplace"
        )
        skills = _by_name(_load([registration]))
        assert skills["github"] == PUBLIC_GITHUB
        assert "org-unique" not in skills


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
        registration = _registration(make_standalone_marketplace(tmp_path / "org"))
        skills = _by_name(
            _load(
                [registration],
                load_public=load_public,
                load_registered_marketplaces=load_registered,
            )
        )
        assert ("public-only" in skills) is public
        assert ("org-unique" in skills) is org

    @pytest.mark.parametrize("public_env", ["true", "false"])
    def test_route_accepts_independent_flag(self, tmp_path, monkeypatch, public_env):
        monkeypatch.setenv(PUBLIC_SKILLS_ENABLED_ENV, public_env)
        org = make_standalone_marketplace(tmp_path / "org")
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
