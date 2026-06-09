"""Tests for skills router endpoints."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import api
from openhands.agent_server.skills_service import SkillLoadResult
from openhands.sdk.context.skills import KeywordTrigger, Skill


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(api)


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

    def test_get_skills_with_org_config(self, client):
        """Test skills request with organization configuration."""
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
            call_kwargs = mock_load.call_args[1]
            assert call_kwargs["org_repo_url"] == "https://github.com/myorg/.openhands"
            assert call_kwargs["org_name"] == "myorg"

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


class TestServerDefaultMarketplaces:
    """Integration tests: OH_REGISTERED_MARKETPLACES → /skills request flow.

    These exercise the path that lets a self-hosted/Enterprise agent-server
    image redirect public skill loading without app-server or UI changes:
    operator sets the env var → Config.registered_marketplaces → merged into
    SkillsRequest.registered_marketplaces by skills_router → forwarded to
    load_all_skills.
    """

    @staticmethod
    def _capture_kwargs(holder: dict):
        """Build a mock side_effect that captures kwargs and returns an empty result."""

        def _side_effect(*args, **kwargs):
            holder.update(kwargs)
            return SkillLoadResult(skills=[], sources={})

        return _side_effect

    def test_server_default_used_when_request_omits_marketplaces(self, client):
        """With OH_REGISTERED_MARKETPLACES set and an empty request, the server
        default is forwarded to load_all_skills."""
        from openhands.agent_server.config import Config
        from openhands.sdk.plugin.types import MarketplaceRegistration

        server_default = MarketplaceRegistration(
            name="public",
            source="https://internal/extensions.git",
            ref="v1.4",
            auto_load="all",
        )
        captured: dict = {}

        # Build a Config whose registered_marketplaces match what the env-var
        # parser would have produced at startup. Override get_default_config so
        # the router sees it. Real env-var → Config plumbing is covered by
        # test_env_parser.test_config_registered_marketplaces_parsing.
        with (
            patch(
                "openhands.agent_server.skills_router.get_default_config",
                return_value=Config(registered_marketplaces=[server_default]),
            ),
            patch(
                "openhands.agent_server.skills_router.load_all_skills",
                side_effect=self._capture_kwargs(captured),
            ),
        ):
            response = client.post("/api/skills", json={})

        assert response.status_code == 200
        regs = captured["registered_marketplaces"]
        assert len(regs) == 1
        assert regs[0].name == "public"
        assert regs[0].source == "https://internal/extensions.git"
        assert regs[0].ref == "v1.4"

    def test_request_overrides_shadow_server_default_by_name(self, client):
        """Per-request `public` registration replaces the server's `public`
        default; non-overlapping server defaults are still forwarded."""
        from openhands.agent_server.config import Config
        from openhands.sdk.plugin.types import MarketplaceRegistration

        server_defaults = [
            MarketplaceRegistration(
                name="public",
                source="https://internal/extensions.git",
                auto_load="all",
            ),
            MarketplaceRegistration(
                name="team",
                source="github:acme/team-skills",
                auto_load="all",
            ),
        ]
        captured: dict = {}

        request_body = {
            "registered_marketplaces": [
                {
                    "name": "public",
                    "source": "github:OpenHands/extensions",
                    "auto_load": "all",
                }
            ]
        }

        with (
            patch(
                "openhands.agent_server.skills_router.get_default_config",
                return_value=Config(registered_marketplaces=server_defaults),
            ),
            patch(
                "openhands.agent_server.skills_router.load_all_skills",
                side_effect=self._capture_kwargs(captured),
            ),
        ):
            response = client.post("/api/skills", json=request_body)

        assert response.status_code == 200
        regs = captured["registered_marketplaces"]
        # 2 entries: request `public` (shadowed server `public`) + server `team`.
        names_to_sources = {r.name: r.source for r in regs}
        assert names_to_sources["public"] == "github:OpenHands/extensions"
        assert names_to_sources["team"] == "github:acme/team-skills"
        assert len(regs) == 2

    def test_no_server_default_falls_back_to_request_only(self, client):
        """With an empty Config.registered_marketplaces, behavior is identical
        to the pre-PR path: only what's in the request body is forwarded."""
        from openhands.agent_server.config import Config

        captured: dict = {}
        with (
            patch(
                "openhands.agent_server.skills_router.get_default_config",
                return_value=Config(registered_marketplaces=[]),
            ),
            patch(
                "openhands.agent_server.skills_router.load_all_skills",
                side_effect=self._capture_kwargs(captured),
            ),
        ):
            response = client.post("/api/skills", json={})

        assert response.status_code == 200
        assert captured["registered_marketplaces"] == []
