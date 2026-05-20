"""Tests for the route-level ``agent.agent_context.skills`` trim.

The four read endpoints (``GET /search``, ``GET /{id}``, ``GET ""``,
``POST ""``) on the conversation router strip ``agent.agent_context.skills``
from the response payload. The persisted ``ConversationState`` and the
in-memory copy held by the agent's runtime are unaffected — only the
bytes leaving over HTTP shrink.

See the SDK PR description for why this lives at the route boundary
rather than inside ``AgentContext`` itself.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.models import (
    ConversationInfo,
    ConversationPage,
    trim_conversation_response_skills,
)
from openhands.agent_server.utils import utc_now
from openhands.sdk import LLM, Agent
from openhands.sdk.context import AgentContext
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.skills import Skill
from openhands.sdk.workspace import LocalWorkspace


def _make_skill(name: str, content: str = "skill body bytes") -> Skill:
    return Skill(name=name, content=content, source=f"/fake/{name}.md")


def _make_conversation_with_skills(skills: list[Skill]) -> ConversationInfo:
    """Build a ``ConversationInfo`` whose agent carries ``skills``.

    The full ``AgentContext`` field set is otherwise empty so the
    trimmed payload reflects only the skills delta.
    """
    now = utc_now()
    return ConversationInfo(
        id=uuid4(),
        agent=Agent(
            llm=LLM(model="gpt-4o", api_key=SecretStr("k"), usage_id="test-llm"),
            tools=[],
            agent_context=AgentContext(skills=skills),
        ),
        workspace=LocalWorkspace(working_dir="/tmp/test"),
        execution_status=ConversationExecutionStatus.IDLE,
        title="Test",
        created_at=now,
        updated_at=now,
    )


class TestTrimHelper:
    """Unit tests for the pure-function helper."""

    def test_strips_skills_when_present(self):
        info = _make_conversation_with_skills(
            [_make_skill("a"), _make_skill("b"), _make_skill("c")]
        )
        trimmed = trim_conversation_response_skills(info)
        assert trimmed.agent.agent_context is not None
        assert trimmed.agent.agent_context.skills == []

    def test_returns_same_instance_when_nothing_to_strip(self):
        # Empty skill list → identity return (no needless model_copy).
        info = _make_conversation_with_skills([])
        trimmed = trim_conversation_response_skills(info)
        assert trimmed is info

    def test_does_not_touch_other_agent_context_fields(self):
        info = _make_conversation_with_skills([_make_skill("a")])
        # Mutate a non-skill field so we can assert it survives.
        assert info.agent.agent_context is not None
        info = info.model_copy(
            update={
                "agent": info.agent.model_copy(
                    update={
                        "agent_context": info.agent.agent_context.model_copy(
                            update={"system_message_suffix": "carry me through"}
                        )
                    }
                )
            }
        )
        trimmed = trim_conversation_response_skills(info)
        assert trimmed.agent.agent_context is not None
        assert trimmed.agent.agent_context.skills == []
        assert trimmed.agent.agent_context.system_message_suffix == "carry me through"

    def test_does_not_mutate_input(self):
        info = _make_conversation_with_skills([_make_skill("a"), _make_skill("b")])
        trim_conversation_response_skills(info)
        # Caller's reference still sees the full skills — model_copy
        # gave us a fresh instance, the input is untouched.
        assert info.agent.agent_context is not None
        assert {s.name for s in info.agent.agent_context.skills} == {"a", "b"}

    def test_agent_without_agent_context_passes_through(self):
        now = utc_now()
        info = ConversationInfo(
            id=uuid4(),
            agent=Agent(
                llm=LLM(model="gpt-4o", api_key=SecretStr("k"), usage_id="t"),
                tools=[],
            ),
            workspace=LocalWorkspace(working_dir="/tmp/test"),
            execution_status=ConversationExecutionStatus.IDLE,
            title="Test",
            created_at=now,
            updated_at=now,
        )
        # No agent_context at all → helper is a no-op.
        assert trim_conversation_response_skills(info) is info


class TestRouteIntegration:
    """Integration tests through the FastAPI router — proves the trim
    actually fires at every read endpoint."""

    @pytest.fixture
    def heavy_conversation(self):
        # 5 skills with non-trivial content — enough that the trim
        # is visible in the serialized JSON byte count.
        return _make_conversation_with_skills(
            [_make_skill(f"skill-{i}", "x" * 500) for i in range(5)]
        )

    @pytest.fixture
    def client(self, heavy_conversation):
        service = AsyncMock(spec=ConversationService)
        service.get_conversation.return_value = heavy_conversation
        service.batch_get_conversations.return_value = [heavy_conversation]
        service.search_conversations.return_value = ConversationPage(
            items=[heavy_conversation], next_page_id=None
        )

        app = FastAPI()
        app.include_router(conversation_router, prefix="/api")
        app.state.config = Config(
            static_files_path=None, session_api_keys=[], secret_key=None
        )
        app.dependency_overrides[get_conversation_service] = lambda: service
        return TestClient(app), heavy_conversation

    def test_get_conversation_trims_skills(self, client):
        c, heavy = client
        response = c.get(f"/api/conversations/{heavy.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["agent"]["agent_context"]["skills"] == []

    def test_batch_get_conversations_trims_skills(self, client):
        c, heavy = client
        response = c.get(f"/api/conversations?ids={heavy.id}")
        assert response.status_code == 200
        body = response.json()
        assert body[0]["agent"]["agent_context"]["skills"] == []

    def test_batch_get_handles_null_items(self):
        """Missing items return ``None`` and the trim doesn't crash on them."""
        service = AsyncMock(spec=ConversationService)
        service.batch_get_conversations.return_value = [None]
        app = FastAPI()
        app.include_router(conversation_router, prefix="/api")
        app.state.config = Config(
            static_files_path=None, session_api_keys=[], secret_key=None
        )
        app.dependency_overrides[get_conversation_service] = lambda: service
        c = TestClient(app)
        response = c.get(f"/api/conversations?ids={uuid4()}")
        assert response.status_code == 200
        assert response.json() == [None]

    def test_search_conversations_trims_skills(self, client):
        c, _heavy = client
        response = c.get("/api/conversations/search")
        assert response.status_code == 200
        body = response.json()
        assert body["items"][0]["agent"]["agent_context"]["skills"] == []

    def test_response_size_drops_meaningfully(self, client):
        """Compare trimmed (HTTP) vs untrimmed (model_dump_json) sizes.

        The conversation has 5 skills × 500 chars of content = ~2500
        bytes of skill bodies. The trimmed HTTP response should be at
        least that much smaller than serializing the same conversation
        with skills intact.
        """
        c, heavy = client
        response = c.get("/api/conversations/search")
        trimmed_bytes = len(response.content)
        untrimmed_bytes = len(
            ConversationPage(items=[heavy], next_page_id=None).model_dump_json()
        )
        # 5 × 500 chars of "x" skill content + per-skill metadata
        # overhead. Conservatively require at least 1500 bytes shaved.
        assert untrimmed_bytes - trimmed_bytes > 1500, (
            f"trim should drop ~2500 B of skill content; got "
            f"{untrimmed_bytes - trimmed_bytes} B saved "
            f"({untrimmed_bytes} → {trimmed_bytes})"
        )
