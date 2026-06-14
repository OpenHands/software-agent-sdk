"""Tests for skills router endpoints."""

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.skills_service import MarketplaceSkillInfo, SkillLoadResult
from openhands.sdk.extensions.fetch import ExtensionFetchError
from openhands.sdk.skills import (
    InstalledSkillInfo,
    KeywordTrigger,
    Skill,
    SkillFetchError,
    SkillValidationError,
)


@pytest.fixture
def client():
    """Create a test client for the FastAPI app without authentication."""
    config = Config(session_api_keys=[])  # Disable authentication
    return TestClient(create_app(config), raise_server_exceptions=False)


@pytest.fixture
def mock_installed_skill_info():
    """Create a mock InstalledSkillInfo for testing."""
    return InstalledSkillInfo(
        name="test-skill",
        version="1.0.0",
        description="A test skill",
        enabled=True,
        source="github:owner/repo/skills/test-skill",
        resolved_ref="abc123",
        repo_path=None,
        installed_at="2024-01-01T00:00:00Z",
        install_path=Path("/home/user/.openhands/skills/installed/test-skill"),
    )


class TestGetSkillsEndpoint:
    """Tests for POST /skills endpoint."""

    def test_get_skills_default_request(self, client):
        """Test default skills request with all sources enabled."""
        with patch("openhands.agent_server.skills_router.load_all_skills") as mock_load:
            mock_load.return_value = SkillLoadResult(
                skills=[
                    Skill(name="test-skill", content="content", trigger=None),
                ],
                sources={"public": 1, "user": 0, "project": 0, "org": 0, "sandbox": 0},
            )

            response = client.post("/api/skills", json={})

            assert response.status_code == 200
            data = response.json()
            assert "skills" in data
            assert "sources" in data
            assert len(data["skills"]) == 1
            assert data["skills"][0]["name"] == "test-skill"

    def test_get_skills_with_project_dir(self, client):
        """Test skills request with project directory."""
        with patch("openhands.agent_server.skills_router.load_all_skills") as mock_load:
            mock_load.return_value = SkillLoadResult(skills=[], sources={})

            response = client.post(
                "/api/skills",
                json={
                    "project_dir": "/workspace/myproject",
                    "load_project": True,
                },
            )

            assert response.status_code == 200
            mock_load.assert_called_once()
            call_kwargs = mock_load.call_args[1]
            assert call_kwargs["project_dir"] == "/workspace/myproject"
            assert call_kwargs["load_project"] is True

    def test_get_skills_legacy_org_config_supported(self, client):
        """A deprecated single org_config is normalized to a one-entry org_repos."""
        with patch("openhands.agent_server.skills_router.load_all_skills") as mock_load:
            mock_load.return_value = SkillLoadResult(skills=[], sources={})

            response = client.post(
                "/api/skills",
                json={
                    "load_org": True,
                    "org_config": {
                        "repository": "myorg/myrepo",
                        "provider": "github",
                        "org_repo_url": "https://github.com/myorg/.openhands",
                        "org_name": "myorg",
                    },
                },
            )

            assert response.status_code == 200
            mock_load.assert_called_once()
            assert mock_load.call_args[1]["org_repos"] == [
                ("https://github.com/myorg/.openhands", "myorg")
            ]

    def test_get_skills_with_org_configs_list(self, client):
        """Multiple org_configs are forwarded as an ordered list of (url, name)."""
        with patch("openhands.agent_server.skills_router.load_all_skills") as mock_load:
            mock_load.return_value = SkillLoadResult(skills=[], sources={})

            response = client.post(
                "/api/skills",
                json={
                    "load_org": True,
                    "org_configs": [
                        {
                            "repository": "hieptl/.openhands",
                            "provider": "github",
                            "org_repo_url": "https://github.com/hieptl/.openhands",
                            "org_name": "hieptl",
                        },
                        {
                            "repository": "hieptl/.agents",
                            "provider": "github",
                            "org_repo_url": "https://github.com/hieptl/.agents",
                            "org_name": "hieptl",
                        },
                    ],
                },
            )

            assert response.status_code == 200
            mock_load.assert_called_once()
            assert mock_load.call_args[1]["org_repos"] == [
                ("https://github.com/hieptl/.openhands", "hieptl"),
                ("https://github.com/hieptl/.agents", "hieptl"),
            ]

    def test_get_skills_with_sandbox_config(self, client):
        """Test skills request with sandbox configuration."""
        with patch("openhands.agent_server.skills_router.load_all_skills") as mock_load:
            mock_load.return_value = SkillLoadResult(
                skills=[Skill(name="work_hosts", content="host info", trigger=None)],
                sources={"sandbox": 1},
            )

            response = client.post(
                "/api/skills",
                json={
                    "sandbox_config": {
                        "exposed_urls": [
                            {
                                "name": "WORKER_8080",
                                "url": "http://localhost:8080",
                                "port": 8080,
                            }
                        ]
                    }
                },
            )

            assert response.status_code == 200
            mock_load.assert_called_once()
            call_kwargs = mock_load.call_args[1]
            assert call_kwargs["sandbox_exposed_urls"] is not None
            assert len(call_kwargs["sandbox_exposed_urls"]) == 1
            assert call_kwargs["sandbox_exposed_urls"][0].name == "WORKER_8080"

    def test_get_skills_disabled_sources(self, client):
        """Test skills request with sources disabled."""
        with patch("openhands.agent_server.skills_router.load_all_skills") as mock_load:
            mock_load.return_value = SkillLoadResult(skills=[], sources={})

            response = client.post(
                "/api/skills",
                json={
                    "load_public": False,
                    "load_user": False,
                    "load_project": False,
                    "load_org": False,
                },
            )

            assert response.status_code == 200
            mock_load.assert_called_once()
            call_kwargs = mock_load.call_args[1]
            assert call_kwargs["load_public"] is False
            assert call_kwargs["load_user"] is False
            assert call_kwargs["load_project"] is False
            assert call_kwargs["load_org"] is False

    def test_get_skills_converts_skill_to_skill_info(self, client):
        """Test that Skill objects are properly converted to SkillInfo format."""
        with patch("openhands.agent_server.skills_router.load_all_skills") as mock_load:
            mock_load.return_value = SkillLoadResult(
                skills=[
                    Skill(
                        name="knowledge-skill",
                        content="knowledge content",
                        trigger=KeywordTrigger(keywords=["python", "coding"]),
                        source="/path/to/skill.md",
                        description="A knowledge skill",
                    ),
                ],
                sources={"public": 1},
            )

            response = client.post("/api/skills", json={})

            assert response.status_code == 200
            data = response.json()
            skill_info = data["skills"][0]
            assert skill_info["name"] == "knowledge-skill"
            assert skill_info["type"] == "knowledge"
            assert skill_info["content"] == "knowledge content"
            assert skill_info["triggers"] == ["python", "coding"]
            assert skill_info["source"] == "/path/to/skill.md"
            assert skill_info["description"] == "A knowledge skill"
            assert skill_info["is_agentskills_format"] is False

    def test_get_skills_agent_skill_format(self, client):
        """Test that AgentSkills format is correctly represented."""
        with patch("openhands.agent_server.skills_router.load_all_skills") as mock_load:
            mock_load.return_value = SkillLoadResult(
                skills=[
                    Skill(
                        name="agent-skill",
                        content="agent content",
                        trigger=None,
                        is_agentskills_format=True,
                        disable_model_invocation=True,
                        allowed_tools=["Bash", "Read"],
                    ),
                ],
                sources={"public": 1},
            )

            response = client.post("/api/skills", json={})

            assert response.status_code == 200
            data = response.json()
            skill_info = data["skills"][0]
            assert skill_info["type"] == "agentskills"
            assert skill_info["is_agentskills_format"] is True
            assert skill_info["disable_model_invocation"] is True
            assert skill_info["allowed_tools"] == ["Bash", "Read"]

    def test_get_skills_response_sources(self, client):
        """Test that source counts are included in response."""
        with patch("openhands.agent_server.skills_router.load_all_skills") as mock_load:
            mock_load.return_value = SkillLoadResult(
                skills=[],
                sources={
                    "public": 10,
                    "user": 5,
                    "project": 3,
                    "org": 2,
                    "sandbox": 1,
                },
            )

            response = client.post("/api/skills", json={})

            assert response.status_code == 200
            data = response.json()
            assert data["sources"]["public"] == 10
            assert data["sources"]["user"] == 5
            assert data["sources"]["project"] == 3
            assert data["sources"]["org"] == 2
            assert data["sources"]["sandbox"] == 1


class TestSyncSkillsEndpoint:
    """Tests for POST /skills/sync endpoint."""

    def test_sync_skills_success(self, client):
        """Test successful skills sync."""
        with patch(
            "openhands.agent_server.skills_router.sync_public_skills"
        ) as mock_sync:
            mock_sync.return_value = (True, "Skills synced successfully")

            response = client.post("/api/skills/sync")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert "synced" in data["message"].lower()

    def test_sync_skills_failure(self, client):
        """Test failed skills sync."""
        with patch(
            "openhands.agent_server.skills_router.sync_public_skills"
        ) as mock_sync:
            mock_sync.return_value = (False, "Network error occurred")

            response = client.post("/api/skills/sync")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"
            msg_lower = data["message"].lower()
            assert "error" in msg_lower or "network" in msg_lower


class TestPydanticModels:
    """Tests for Pydantic model validation."""

    def test_exposed_url_validation(self, client):
        """Test ExposedUrl model validation."""
        with patch("openhands.agent_server.skills_router.load_all_skills") as mock_load:
            mock_load.return_value = SkillLoadResult(skills=[], sources={})

            # Valid exposed URL
            response = client.post(
                "/api/skills",
                json={
                    "sandbox_config": {
                        "exposed_urls": [
                            {
                                "name": "WORKER_8080",
                                "url": "http://localhost:8080",
                                "port": 8080,
                            }
                        ]
                    }
                },
            )
            assert response.status_code == 200

    def test_org_config_validation(self, client):
        """Test OrgConfig model validation."""
        with patch("openhands.agent_server.skills_router.load_all_skills") as mock_load:
            mock_load.return_value = SkillLoadResult(skills=[], sources={})

            # Valid org config
            response = client.post(
                "/api/skills",
                json={
                    "org_config": {
                        "repository": "org/repo",
                        "provider": "github",
                        "org_repo_url": "https://github.com/org/.openhands",
                        "org_name": "org",
                    }
                },
            )
            assert response.status_code == 200

    def test_invalid_request_body(self, client):
        """Test handling of invalid request body."""
        # Send invalid JSON structure
        response = client.post(
            "/api/skills",
            json={"load_public": "not_a_boolean"},
        )
        # FastAPI returns 422 for validation errors
        assert response.status_code == 422

    def test_missing_required_org_config_fields(self, client):
        """Test validation when org_config is missing required fields."""
        response = client.post(
            "/api/skills",
            json={
                "org_config": {
                    "repository": "org/repo",
                    # Missing provider, org_repo_url, org_name
                }
            },
        )
        assert response.status_code == 422


class TestInstallSkillEndpoint:
    """Tests for POST /skills/install endpoint."""

    def test_install_skill_success(self, client, mock_installed_skill_info):
        """Test successful skill installation."""
        with patch(
            "openhands.agent_server.skills_router.service_install_skill"
        ) as mock_install:
            mock_install.return_value = mock_installed_skill_info

            response = client.post(
                "/api/skills/install",
                json={"source": "github:owner/repo/skills/test-skill"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "test-skill"
            assert data["source"] == "github:owner/repo/skills/test-skill"
            assert data["enabled"] is True

    def test_install_skill_with_force(self, client, mock_installed_skill_info):
        """Test skill installation with force option."""
        with patch(
            "openhands.agent_server.skills_router.service_install_skill"
        ) as mock_install:
            mock_install.return_value = mock_installed_skill_info

            response = client.post(
                "/api/skills/install",
                json={
                    "source": "github:owner/repo/skills/test-skill",
                    "force": True,
                },
            )

            assert response.status_code == 200
            mock_install.assert_called_once()
            call_kwargs = mock_install.call_args[1]
            assert call_kwargs["force"] is True

    def test_install_skill_with_ref(self, client, mock_installed_skill_info):
        """Test skill installation with specific ref."""
        with patch(
            "openhands.agent_server.skills_router.service_install_skill"
        ) as mock_install:
            mock_install.return_value = mock_installed_skill_info

            response = client.post(
                "/api/skills/install",
                json={
                    "source": "github:owner/repo",
                    "ref": "v1.0.0",
                    "repo_path": "skills/test-skill",
                },
            )

            assert response.status_code == 200
            mock_install.assert_called_once()
            call_kwargs = mock_install.call_args[1]
            assert call_kwargs["ref"] == "v1.0.0"
            assert call_kwargs["repo_path"] == "skills/test-skill"

    def test_install_skill_already_exists(self, client):
        """Test skill installation when skill already exists."""
        with patch(
            "openhands.agent_server.skills_router.service_install_skill"
        ) as mock_install:
            mock_install.side_effect = FileExistsError("Skill already exists")

            response = client.post(
                "/api/skills/install",
                json={"source": "github:owner/repo/skills/test-skill"},
            )

            assert response.status_code == 409
            assert "already installed" in response.json()["detail"].lower()

    def test_install_skill_fetch_error(self, client):
        """Test skill installation with fetch error."""
        with patch(
            "openhands.agent_server.skills_router.service_install_skill"
        ) as mock_install:
            mock_install.side_effect = SkillFetchError("Network error")

            response = client.post(
                "/api/skills/install",
                json={"source": "github:owner/repo/skills/test-skill"},
            )

            assert response.status_code == 400
            assert "fetch" in response.json()["detail"].lower()

    def test_install_skill_extension_fetch_error(self, client):
        """ExtensionFetchError (raised by the SDK for GitHub URL/shorthand failures)
        must map to 400, not 500."""
        with patch(
            "openhands.agent_server.skills_router.service_install_skill"
        ) as mock_install:
            mock_install.side_effect = ExtensionFetchError(
                "Could not fetch from GitHub"
            )

            response = client.post(
                "/api/skills/install",
                json={"source": "https://github.com/Owner/repo/tree/main/path"},
            )

            assert response.status_code == 400
            assert "fetch" in response.json()["detail"].lower()

    def test_install_skill_validation_error(self, client):
        """Test skill installation with validation error."""
        with patch(
            "openhands.agent_server.skills_router.service_install_skill"
        ) as mock_install:
            mock_install.side_effect = SkillValidationError("Missing SKILL.md")

            response = client.post(
                "/api/skills/install",
                json={"source": "/path/to/invalid-skill"},
            )

            assert response.status_code == 422
            assert "invalid" in response.json()["detail"].lower()


class TestListInstalledSkillsEndpoint:
    """Tests for GET /skills/installed endpoint."""

    def test_list_installed_skills_empty(self, client):
        """Test listing when no skills are installed."""
        with patch(
            "openhands.agent_server.skills_router.service_list_installed_skills"
        ) as mock_list:
            mock_list.return_value = []

            response = client.get("/api/skills/installed")

            assert response.status_code == 200
            data = response.json()
            assert data["skills"] == []

    def test_list_installed_skills_with_skills(self, client, mock_installed_skill_info):
        """Test listing installed skills."""
        with patch(
            "openhands.agent_server.skills_router.service_list_installed_skills"
        ) as mock_list:
            mock_list.return_value = [mock_installed_skill_info]

            response = client.get("/api/skills/installed")

            assert response.status_code == 200
            data = response.json()
            assert len(data["skills"]) == 1
            assert data["skills"][0]["name"] == "test-skill"


class TestGetInstalledSkillEndpoint:
    """Tests for GET /skills/installed/{skill_name} endpoint."""

    def test_get_installed_skill_found(self, client, mock_installed_skill_info):
        """Test getting an installed skill that exists."""
        with patch(
            "openhands.agent_server.skills_router.service_get_installed_skill"
        ) as mock_get:
            mock_get.return_value = mock_installed_skill_info

            response = client.get("/api/skills/installed/test-skill")

            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "test-skill"

    def test_get_installed_skill_not_found(self, client):
        """Test getting a skill that is not installed."""
        with patch(
            "openhands.agent_server.skills_router.service_get_installed_skill"
        ) as mock_get:
            mock_get.return_value = None

            response = client.get("/api/skills/installed/nonexistent")

            assert response.status_code == 404
            assert "not installed" in response.json()["detail"].lower()


class TestUpdateSkillStateEndpoint:
    """Tests for PATCH /skills/installed/{skill_name} endpoint."""

    def test_enable_skill_success(self, client):
        """Test enabling a skill."""
        with patch(
            "openhands.agent_server.skills_router.service_enable_skill"
        ) as mock_enable:
            mock_enable.return_value = True

            response = client.patch(
                "/api/skills/installed/test-skill",
                json={"enabled": True},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "test-skill"
            assert data["enabled"] is True

    def test_disable_skill_success(self, client):
        """Test disabling a skill."""
        with patch(
            "openhands.agent_server.skills_router.service_disable_skill"
        ) as mock_disable:
            mock_disable.return_value = True

            response = client.patch(
                "/api/skills/installed/test-skill",
                json={"enabled": False},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["enabled"] is False

    def test_update_skill_state_not_found(self, client):
        """Test updating state of non-existent skill."""
        with patch(
            "openhands.agent_server.skills_router.service_enable_skill"
        ) as mock_enable:
            mock_enable.return_value = False

            response = client.patch(
                "/api/skills/installed/nonexistent",
                json={"enabled": True},
            )

            assert response.status_code == 404


class TestUninstallSkillEndpoint:
    """Tests for DELETE /skills/installed/{skill_name} endpoint."""

    def test_uninstall_skill_success(self, client):
        """Test successful skill uninstallation."""
        with patch(
            "openhands.agent_server.skills_router.service_uninstall_skill"
        ) as mock_uninstall:
            mock_uninstall.return_value = True

            response = client.delete("/api/skills/installed/test-skill")

            assert response.status_code == 200
            data = response.json()
            assert "uninstalled" in data["message"].lower()

    def test_uninstall_skill_not_found(self, client):
        """Test uninstalling a non-existent skill."""
        with patch(
            "openhands.agent_server.skills_router.service_uninstall_skill"
        ) as mock_uninstall:
            mock_uninstall.return_value = False

            response = client.delete("/api/skills/installed/nonexistent")

            assert response.status_code == 404


class TestRefreshSkillEndpoint:
    """Tests for POST /skills/installed/{skill_name}/refresh endpoint."""

    def test_refresh_skill_success(self, client, mock_installed_skill_info):
        """Test successful skill refresh."""
        with patch(
            "openhands.agent_server.skills_router.service_update_skill"
        ) as mock_update:
            mock_update.return_value = mock_installed_skill_info

            response = client.post("/api/skills/installed/test-skill/refresh")

            assert response.status_code == 200
            data = response.json()
            assert data["skill"]["name"] == "test-skill"

    def test_refresh_skill_not_found(self, client):
        """Test refreshing a non-existent skill."""
        with patch(
            "openhands.agent_server.skills_router.service_update_skill"
        ) as mock_update:
            mock_update.return_value = None

            response = client.post("/api/skills/installed/nonexistent/refresh")

            assert response.status_code == 404


class TestMarketplaceCatalogEndpoint:
    """Tests for GET /skills/marketplace endpoint."""

    def test_get_marketplace_catalog_empty(self, client):
        """Test getting marketplace when no skills are available."""
        with patch(
            "openhands.agent_server.skills_router.service_get_marketplace_catalog"
        ) as mock_catalog:
            mock_catalog.return_value = []

            response = client.get("/api/skills/marketplace")

            assert response.status_code == 200
            data = response.json()
            assert data["skills"] == []

    def test_get_marketplace_catalog_with_skills(self, client):
        """Test getting marketplace with available skills."""
        with patch(
            "openhands.agent_server.skills_router.service_get_marketplace_catalog"
        ) as mock_catalog:
            mock_catalog.return_value = [
                MarketplaceSkillInfo(
                    name="github",
                    description="GitHub integration skill",
                    source="github:OpenHands/extensions/skills/github",
                    installed=True,
                ),
                MarketplaceSkillInfo(
                    name="docker",
                    description="Docker management skill",
                    source="github:OpenHands/extensions/skills/docker",
                    installed=False,
                ),
            ]

            response = client.get("/api/skills/marketplace")

            assert response.status_code == 200
            data = response.json()
            assert len(data["skills"]) == 2

            # Check first skill
            assert data["skills"][0]["name"] == "github"
            assert data["skills"][0]["description"] == "GitHub integration skill"
            assert data["skills"][0]["installed"] is True

            # Check second skill
            assert data["skills"][1]["name"] == "docker"
            assert data["skills"][1]["installed"] is False

    def test_get_marketplace_catalog_skill_without_description(self, client):
        """Test marketplace skill with no description."""
        with patch(
            "openhands.agent_server.skills_router.service_get_marketplace_catalog"
        ) as mock_catalog:
            mock_catalog.return_value = [
                MarketplaceSkillInfo(
                    name="minimal-skill",
                    description=None,
                    source="github:owner/repo",
                    installed=False,
                ),
            ]

            response = client.get("/api/skills/marketplace")

            assert response.status_code == 200
            data = response.json()
            assert len(data["skills"]) == 1
            assert data["skills"][0]["description"] is None
