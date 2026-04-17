from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config


def test_get_agent_settings_schema():
    client = TestClient(create_app(Config(static_files_path=None, session_api_keys=[])))

    response = client.get("/api/settings/agent-schema")

    assert response.status_code == 200
    body = response.json()
    assert body["model_name"] == "AgentSettings"

    section_keys = [section["key"] for section in body["sections"]]
    assert "llm" in section_keys
    assert "condenser" in section_keys
    assert "verification" in section_keys

    verification_section = next(
        section for section in body["sections"] if section["key"] == "verification"
    )
    verification_field_keys = {field["key"] for field in verification_section["fields"]}
    assert "verification.critic_enabled" in verification_field_keys
    assert "confirmation_mode" not in verification_field_keys
    assert "security_analyzer" not in verification_field_keys


def test_get_conversation_settings_schema():
    client = TestClient(create_app(Config(static_files_path=None, session_api_keys=[])))

    response = client.get("/api/settings/conversation-schema")

    assert response.status_code == 200
    body = response.json()
    assert body["model_name"] == "ConversationSettings"

    section_keys = [section["key"] for section in body["sections"]]
    assert section_keys == ["general", "verification"]

    verification_section = next(
        section for section in body["sections"] if section["key"] == "verification"
    )
    verification_field_keys = {field["key"] for field in verification_section["fields"]}
    assert "confirmation_mode" in verification_field_keys
    assert "security_analyzer" in verification_field_keys
