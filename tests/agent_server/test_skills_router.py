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
