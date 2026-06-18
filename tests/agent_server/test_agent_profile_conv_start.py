"""Tests for agent_profile_id at conversation start + LaunchedProfile provenance.

Covers:
- start-from-profile (OpenHands + ACP paths)
- mutual-exclusivity validation (SDK layer)
- unknown-id 404 / dangling-ref 422 (router layer)
- LaunchedProfile provenance round-trip through StoredConversation
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import (
    ConversationInfo,
    LaunchedProfile,
    StartConversationRequest,
    StoredConversation,
)
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.profiles.agent_profile import (
    ACPAgentProfile,
    OpenHandsAgentProfile,
)
from openhands.sdk.profiles.resolver import DanglingMcpServerRef, ProfileNotFound
from openhands.sdk.workspace import LocalWorkspace


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """TestClient with no auth — conversations router only."""
    app = FastAPI()
    app.include_router(conversation_router, prefix="/api")
    app.state.config = Config(
        static_files_path=None, session_api_keys=[], secret_key=None
    )
    return TestClient(app)


@pytest.fixture
def mock_conversation_service():
    return AsyncMock(spec=ConversationService)


def _make_openhands_profile(profile_id: UUID | None = None) -> OpenHandsAgentProfile:
    return OpenHandsAgentProfile(
        id=profile_id or uuid4(),
        name="my-profile",
        revision=3,
        llm_profile_ref="default",
    )


def _make_acp_profile(profile_id: UUID | None = None) -> ACPAgentProfile:
    return ACPAgentProfile(
        id=profile_id or uuid4(),
        name="acp-profile",
        revision=1,
        acp_server="claude-code",
    )


def _make_agent() -> Agent:
    return Agent(llm=LLM(model="gpt-4o", usage_id="llm"), tools=[])


# ---------------------------------------------------------------------------
# SDK-layer: mutual exclusivity (StartConversationRequest)
# ---------------------------------------------------------------------------


class TestStartConversationRequestValidation:
    def test_agent_profile_id_alone_is_valid(self):
        req = StartConversationRequest(
            agent_profile_id=uuid4(),
            workspace=LocalWorkspace(working_dir="/tmp"),
        )
        assert req.agent_profile_id is not None
        assert req.agent is None

    def test_agent_alone_is_valid(self):
        req = StartConversationRequest(
            agent=_make_agent(),
            workspace=LocalWorkspace(working_dir="/tmp"),
        )
        assert req.agent is not None
        assert req.agent_profile_id is None

    def test_agent_profile_id_and_agent_is_invalid(self):
        with pytest.raises(ValidationError, match="mutually exclusive"):
            StartConversationRequest(
                agent_profile_id=uuid4(),
                agent=_make_agent(),
                workspace=LocalWorkspace(working_dir="/tmp"),
            )

    def test_agent_profile_id_and_agent_settings_is_invalid(self):
        with pytest.raises(ValidationError, match="mutually exclusive"):
            StartConversationRequest(
                agent_profile_id=uuid4(),
                agent_settings={
                    "agent_kind": "openhands",
                    "llm": {"model": "gpt-4o", "usage_id": "llm"},
                },
                workspace=LocalWorkspace(working_dir="/tmp"),
            )

    def test_no_agent_source_is_invalid(self):
        with pytest.raises(ValidationError, match="agent_profile_id"):
            StartConversationRequest(workspace=LocalWorkspace(working_dir="/tmp"))

    def test_agent_profile_id_is_excluded_from_model_dump(self):
        """agent_profile_id is carry-only (exclude=True)."""
        agent = _make_agent()
        req = StartConversationRequest(
            agent=agent,
            workspace=LocalWorkspace(working_dir="/tmp"),
        )
        dumped = req.model_dump(mode="json")
        assert "agent_profile_id" not in dumped


# ---------------------------------------------------------------------------
# Service-layer: _resolve_agent_from_profile helper
# ---------------------------------------------------------------------------

# The helper does local imports inside the function body; patch at the source modules.
_STORE_PATH = "openhands.sdk.profiles.agent_profile_store.AgentProfileStore"
_LLM_STORE_PATH = "openhands.sdk.llm.llm_profile_store.LLMProfileStore"
_SETTINGS_STORE_PATH = "openhands.agent_server.persistence.get_settings_store"
_RESOLVE_PATH = "openhands.sdk.profiles.resolver.resolve_agent_profile"


class TestResolveAgentFromProfile:
    def test_unknown_id_raises_profile_not_found(self):
        from openhands.agent_server.conversation_service import (
            _resolve_agent_from_profile,
        )

        with patch(_STORE_PATH) as MockStore:
            MockStore.return_value.name_for_id.return_value = None
            with pytest.raises(ProfileNotFound, match="not found"):
                _resolve_agent_from_profile(uuid4(), cipher=None)

    def test_openhands_profile_resolves_to_agent_and_stamps_launched(self):
        from openhands.agent_server.conversation_service import (
            _resolve_agent_from_profile,
        )

        profile = _make_openhands_profile()
        agent = _make_agent()

        with (
            patch(_STORE_PATH) as MockStore,
            patch(_LLM_STORE_PATH),
            patch(_SETTINGS_STORE_PATH) as MockSettings,
            patch(_RESOLVE_PATH) as MockResolve,
        ):
            store_inst = MockStore.return_value
            store_inst.name_for_id.return_value = profile.name
            store_inst.load.return_value = profile

            MockSettings.return_value.load.return_value = MagicMock(
                agent_settings=MagicMock(mcp_config=None)
            )
            mock_config = MagicMock()
            mock_config.create_agent.return_value = agent
            MockResolve.return_value = mock_config

            result_agent, launched = _resolve_agent_from_profile(
                profile.id, cipher=None
            )

        assert result_agent is agent
        assert launched.profile_id == profile.id
        assert launched.revision == profile.revision

    def test_dangling_mcp_server_ref_propagates(self):
        from openhands.agent_server.conversation_service import (
            _resolve_agent_from_profile,
        )

        profile = _make_openhands_profile()
        with (
            patch(_STORE_PATH) as MockStore,
            patch(_LLM_STORE_PATH),
            patch(_SETTINGS_STORE_PATH) as MockSettings,
            patch(_RESOLVE_PATH) as MockResolve,
        ):
            store_inst = MockStore.return_value
            store_inst.name_for_id.return_value = profile.name
            store_inst.load.return_value = profile
            MockSettings.return_value.load.return_value = MagicMock(
                agent_settings=MagicMock(mcp_config=None)
            )
            MockResolve.side_effect = DanglingMcpServerRef(["missing-server"])

            with pytest.raises(DanglingMcpServerRef) as exc_info:
                _resolve_agent_from_profile(profile.id, cipher=None)
        assert "missing-server" in exc_info.value.missing

    def test_acp_profile_resolves_to_acp_agent(self):
        from openhands.agent_server.conversation_service import (
            _resolve_agent_from_profile,
        )
        from openhands.sdk.agent.acp_agent import ACPAgent

        profile = _make_acp_profile()
        acp_agent = MagicMock(spec=ACPAgent)

        with (
            patch(_STORE_PATH) as MockStore,
            patch(_LLM_STORE_PATH),
            patch(_SETTINGS_STORE_PATH) as MockSettings,
            patch(_RESOLVE_PATH) as MockResolve,
        ):
            store_inst = MockStore.return_value
            store_inst.name_for_id.return_value = profile.name
            store_inst.load.return_value = profile
            MockSettings.return_value.load.return_value = MagicMock(
                agent_settings=MagicMock(mcp_config=None)
            )
            mock_config = MagicMock()
            mock_config.create_agent.return_value = acp_agent
            MockResolve.return_value = mock_config

            result_agent, launched = _resolve_agent_from_profile(
                profile.id, cipher=None
            )

        assert result_agent is acp_agent
        assert launched.profile_id == profile.id
        assert launched.revision == profile.revision


# ---------------------------------------------------------------------------
# Service-layer: conversation start with agent_profile_id
# ---------------------------------------------------------------------------


class TestConversationServiceStartFromProfile:
    @pytest.mark.asyncio
    async def test_start_from_profile_stamps_launched_profile_on_stored(self, tmp_path):
        """_start_conversation passes launched_profile to StoredConversation."""
        profile_id = uuid4()
        agent = _make_agent()
        launched_profile = LaunchedProfile(profile_id=profile_id, revision=5)
        request = StartConversationRequest(
            agent_profile_id=profile_id,
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
        )

        captured: dict[str, Any] = {}
        mock_state = ConversationState(
            id=uuid4(),
            agent=agent,
            workspace=request.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
        )

        with patch(
            "openhands.agent_server.conversation_service._resolve_agent_from_profile",
            return_value=(agent, launched_profile),
        ):
            service = ConversationService(conversations_dir=tmp_path)
            service._event_services = {}

            with patch.object(
                service, "_start_event_service", new_callable=AsyncMock
            ) as mock_ses:
                mock_es = AsyncMock(spec=EventService)
                mock_es.get_state.return_value = mock_state
                mock_es.stored = MagicMock(
                    launched_profile=launched_profile,
                    client_tools=[],
                    title=None,
                    metrics=None,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )

                async def capture_start(stored):
                    captured["stored"] = stored
                    return mock_es

                mock_ses.side_effect = capture_start

                info, is_new = await service.start_conversation(request)

        stored = captured.get("stored")
        assert stored is not None, "StoredConversation was not captured"
        assert stored.launched_profile is not None
        assert stored.launched_profile.profile_id == profile_id
        assert stored.launched_profile.revision == 5
        # The resolved agent (not None) must be present
        assert stored.agent is not None

    @pytest.mark.asyncio
    async def test_profile_not_found_propagates(self, tmp_path):
        request = StartConversationRequest(
            agent_profile_id=uuid4(),
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
        )

        with patch(
            "openhands.agent_server.conversation_service._resolve_agent_from_profile",
            side_effect=ProfileNotFound("profile not found"),
        ):
            service = ConversationService(conversations_dir=tmp_path)
            service._event_services = {}

            with pytest.raises(ProfileNotFound):
                await service.start_conversation(request)

    @pytest.mark.asyncio
    async def test_dangling_ref_propagates_from_service(self, tmp_path):
        request = StartConversationRequest(
            agent_profile_id=uuid4(),
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
        )

        with patch(
            "openhands.agent_server.conversation_service._resolve_agent_from_profile",
            side_effect=DanglingMcpServerRef(["mcp-server-x"]),
        ):
            service = ConversationService(conversations_dir=tmp_path)
            service._event_services = {}

            with pytest.raises(DanglingMcpServerRef) as exc_info:
                await service.start_conversation(request)
        assert "mcp-server-x" in exc_info.value.missing


# ---------------------------------------------------------------------------
# Router-layer: HTTP error mapping
# ---------------------------------------------------------------------------


class TestConversationRouterProfileErrors:
    def test_profile_not_found_returns_404(self, client, mock_conversation_service):
        mock_conversation_service.start_conversation.side_effect = ProfileNotFound(
            "Agent profile with id 'abc' not found"
        )
        client.app.dependency_overrides[get_conversation_service] = lambda: (
            mock_conversation_service
        )

        payload = {
            "agent_profile_id": str(uuid4()),
            "workspace": {"working_dir": "/tmp/test", "kind": "LocalWorkspace"},
        }
        resp = client.post("/api/conversations", json=payload)
        assert resp.status_code == 404
        assert "not found" in resp.json().get("detail", "").lower()

    def test_dangling_mcp_server_ref_returns_422(
        self, client, mock_conversation_service
    ):
        mock_conversation_service.start_conversation.side_effect = DanglingMcpServerRef(
            ["missing-server", "another-missing"]
        )
        client.app.dependency_overrides[get_conversation_service] = lambda: (
            mock_conversation_service
        )

        payload = {
            "agent_profile_id": str(uuid4()),
            "workspace": {"working_dir": "/tmp/test", "kind": "LocalWorkspace"},
        }
        resp = client.post("/api/conversations", json=payload)
        assert resp.status_code == 422
        detail = resp.json().get("detail", {})
        assert "dangling_mcp_server_refs" in detail
        assert "missing-server" in detail["dangling_mcp_server_refs"]


# ---------------------------------------------------------------------------
# Provenance round-trip: LaunchedProfile survives serialization
# ---------------------------------------------------------------------------


class TestLaunchedProfileRoundTrip:
    def test_launched_profile_survives_stored_conversation_round_trip(self):
        """LaunchedProfile survives model_dump/model_validate round-trip."""
        profile_id = uuid4()
        lp = LaunchedProfile(profile_id=profile_id, revision=7)
        stored = StoredConversation(
            id=uuid4(),
            agent=_make_agent(),
            workspace=LocalWorkspace(working_dir="/tmp"),
            launched_profile=lp,
        )

        dumped = stored.model_dump(mode="json")
        assert dumped["launched_profile"] is not None
        assert dumped["launched_profile"]["profile_id"] == str(profile_id)
        assert dumped["launched_profile"]["revision"] == 7

        reloaded = StoredConversation.model_validate({"id": str(stored.id), **dumped})
        assert reloaded.launched_profile is not None
        assert reloaded.launched_profile.profile_id == profile_id
        assert reloaded.launched_profile.revision == 7

    def test_stored_conversation_without_profile_has_none(self):
        stored = StoredConversation(
            id=uuid4(),
            agent=_make_agent(),
            workspace=LocalWorkspace(working_dir="/tmp"),
        )
        assert stored.launched_profile is None

    def test_launched_profile_in_conversation_info(self):
        profile_id = uuid4()
        lp = LaunchedProfile(profile_id=profile_id, revision=3)
        now = datetime.now(UTC)
        info = ConversationInfo(
            id=uuid4(),
            agent=_make_agent(),
            workspace=LocalWorkspace(working_dir="/tmp"),
            execution_status=ConversationExecutionStatus.IDLE,
            created_at=now,
            updated_at=now,
            launched_profile=lp,
        )
        assert info.launched_profile is not None
        assert info.launched_profile.profile_id == profile_id
        assert info.launched_profile.revision == 3

    def test_conversation_info_without_profile_is_none(self):
        now = datetime.now(UTC)
        info = ConversationInfo(
            id=uuid4(),
            agent=_make_agent(),
            workspace=LocalWorkspace(working_dir="/tmp"),
            execution_status=ConversationExecutionStatus.IDLE,
            created_at=now,
            updated_at=now,
        )
        assert info.launched_profile is None

    def test_launched_profile_survives_json_serialization(self, tmp_path):
        """Simulate meta.json round-trip: dump → write → read → validate."""
        profile_id = uuid4()
        lp = LaunchedProfile(profile_id=profile_id, revision=5)
        stored = StoredConversation(
            id=uuid4(),
            agent=_make_agent(),
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
            launched_profile=lp,
        )
        meta_file = tmp_path / "meta.json"
        meta_file.write_text(stored.model_dump_json())

        reloaded = StoredConversation.model_validate_json(meta_file.read_text())
        assert reloaded.launched_profile is not None
        assert reloaded.launched_profile.profile_id == profile_id
        assert reloaded.launched_profile.revision == 5
