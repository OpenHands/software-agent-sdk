"""Tests for agent_profile_id at conversation start + LaunchedAgentProfile provenance.

Covers:
- start-from-profile (OpenHands + ACP paths)
- mutual-exclusivity validation (SDK layer)
- unknown-id 404 / dangling-ref 422 (router layer)
- LaunchedAgentProfile provenance round-trip through StoredConversation
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
    LaunchedAgentProfile,
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
from openhands.sdk.profiles.resolver import (
    DanglingMcpServerRef,
    ProfileNotFound,
)
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

    def test_agent_profile_id_present_in_request_payload(self):
        """agent_profile_id must survive model_dump() for HTTP transport."""
        profile_id = uuid4()
        req = StartConversationRequest(
            agent_profile_id=profile_id,
            workspace=LocalWorkspace(working_dir="/tmp"),
        )
        dumped = req.model_dump(mode="json")
        assert "agent_profile_id" in dumped
        assert dumped["agent_profile_id"] == str(profile_id)


# ---------------------------------------------------------------------------
# Service-layer: _resolve_agent_from_profile helper
# ---------------------------------------------------------------------------

# The helper does local imports inside the function body; patch at the source modules.
_STORE_PATH = "openhands.agent_server.persistence.store.get_agent_profile_store"
_LLM_STORE_PATH = "openhands.agent_server.persistence.store.get_llm_profile_store"
_RESOLVE_PATH = "openhands.sdk.profiles.resolver.resolve_agent_profile"
# Skill discovery is patched so OpenHands-profile resolves don't hit the network
# (load_all_skills loads public skills from GitHub). conversation_service imports
# discover_profile_skills directly, so patch it in that namespace.
_DISCOVER_PATH = "openhands.agent_server.conversation_service.discover_profile_skills"


class TestResolveAgentFromProfile:
    def test_unknown_id_raises_profile_not_found(self):
        from openhands.agent_server.conversation_service import (
            _resolve_agent_from_profile,
        )

        with patch(_STORE_PATH) as MockStore:
            MockStore.return_value.name_for_id.return_value = None
            with pytest.raises(ProfileNotFound, match="not found"):
                _resolve_agent_from_profile(uuid4(), cipher=None, mcp_config={})

    def test_openhands_profile_resolves_to_agent_and_stamps_launched(self):
        from openhands.agent_server.conversation_service import (
            _resolve_agent_from_profile,
        )

        # OpenHands profiles always discover the catalog (deny-list needs the
        # full set), threaded through to the resolver as available_skills.
        profile = _make_openhands_profile()
        agent = _make_agent()

        with (
            patch(_STORE_PATH) as MockStore,
            patch(_LLM_STORE_PATH),
            patch(_RESOLVE_PATH) as MockResolve,
            patch(_DISCOVER_PATH, return_value=[]) as MockDiscover,
            # Pin the environment probe: tools=None profiles get browser
            # injected iff the host has chromium (covered by the dedicated
            # injection tests below); this test is about resolution plumbing.
            patch(
                "openhands.agent_server.conversation_service.is_tool_usable",
                return_value=False,
            ),
        ):
            store_inst = MockStore.return_value
            store_inst.name_for_id.return_value = profile.name
            store_inst.load.return_value = profile

            mock_config = MagicMock()
            mock_config.create_agent.return_value = agent
            MockResolve.return_value = mock_config

            result_agent, launched = _resolve_agent_from_profile(
                profile.id, cipher=None, mcp_config={}
            )

        assert result_agent is agent
        assert launched.agent_profile_id == profile.id
        assert launched.revision == profile.revision
        # OpenHands discovery always runs; its result is threaded through.
        MockDiscover.assert_called_once()
        assert MockResolve.call_args.kwargs["available_skills"] == []

    def test_openhands_profile_forces_llm_stream_true(self):
        """A profile-launched OpenHands conversation must guarantee on_token
        wiring (#4014): unlike an inline agent_settings launch, a client can't
        set llm.stream ahead of time on a profile's referenced LLM. This
        agent-server layer forces it after resolution — not the SDK resolver,
        which runs for every caller including headless/scripted ones."""
        from openhands.agent_server.conversation_service import (
            _resolve_agent_from_profile,
        )
        from openhands.sdk.settings.model import OpenHandsAgentSettings

        profile = _make_openhands_profile()
        # A real (unmocked) settings object so isinstance(...) narrows for real.
        resolved_settings = OpenHandsAgentSettings(
            llm=LLM(model="gpt-4o", usage_id="agent", stream=False)
        )
        assert resolved_settings.llm.stream is False

        with (
            patch(_STORE_PATH) as MockStore,
            patch(_LLM_STORE_PATH),
            patch(_RESOLVE_PATH, return_value=resolved_settings),
            patch(
                "openhands.agent_server.conversation_service.is_tool_usable",
                return_value=False,
            ),
        ):
            store_inst = MockStore.return_value
            store_inst.name_for_id.return_value = profile.name
            store_inst.load.return_value = profile

            result_agent, _ = _resolve_agent_from_profile(
                profile.id, cipher=None, mcp_config={}
            )

        assert result_agent.llm.stream is True
        # The original resolved settings object is untouched (model_copy, not
        # a mutation), and the referenced LLM profile on disk is never
        # rewritten — unlike a client-side self-heal that persists the flag.
        assert resolved_settings.llm.stream is False

    def test_acp_profile_does_not_force_llm_stream(self):
        """The stream-forcing guarantee is OpenHands-only: ACP agents emit
        their own message chunks through the ACP bridge without exposing an
        LLM the same way (event_service.py's streaming_enabled already treats
        every ACPAgent as streaming-capable regardless of llm.stream)."""
        from openhands.agent_server.conversation_service import (
            _resolve_agent_from_profile,
        )

        profile = _make_acp_profile()
        agent = MagicMock()

        with (
            patch(_STORE_PATH) as MockStore,
            patch(_LLM_STORE_PATH),
            patch(_RESOLVE_PATH) as MockResolve,
        ):
            store_inst = MockStore.return_value
            store_inst.name_for_id.return_value = profile.name
            store_inst.load.return_value = profile
            mock_config = MagicMock()
            mock_config.create_agent.return_value = agent
            MockResolve.return_value = mock_config

            _resolve_agent_from_profile(profile.id, cipher=None, mcp_config={})

        # No model_copy/mutation attempted on an ACP (non-OpenHandsAgentSettings)
        # resolved settings object.
        mock_config.model_copy.assert_not_called()

    def test_openhands_default_tools_get_browser_when_usable(self):
        """A default-toolset (tools=None) OpenHands profile launch injects the
        browser tool set when this server's runtime can run it — the
        serving-layer counterpart of the SDK's deterministic default (#3978)."""
        from openhands.agent_server.conversation_service import (
            _resolve_agent_from_profile,
        )

        profile = _make_openhands_profile()
        assert profile.tools is None
        agent = _make_agent()

        with (
            patch(_STORE_PATH) as MockStore,
            patch(_LLM_STORE_PATH),
            patch(_RESOLVE_PATH) as MockResolve,
            patch(
                "openhands.agent_server.conversation_service.is_tool_usable",
                return_value=True,
            ) as MockUsable,
        ):
            store_inst = MockStore.return_value
            store_inst.name_for_id.return_value = profile.name
            store_inst.load.return_value = profile
            mock_config = MagicMock()
            mock_config.create_agent.return_value = agent
            MockResolve.return_value = mock_config

            result_agent, _ = _resolve_agent_from_profile(
                profile.id, cipher=None, mcp_config={}
            )

        MockUsable.assert_called_once_with("browser_tool_set")
        assert [tool.name for tool in result_agent.tools] == ["browser_tool_set"]

    def test_openhands_default_tools_skip_browser_when_unusable(self):
        from openhands.agent_server.conversation_service import (
            _resolve_agent_from_profile,
        )

        profile = _make_openhands_profile()
        agent = _make_agent()

        with (
            patch(_STORE_PATH) as MockStore,
            patch(_LLM_STORE_PATH),
            patch(_RESOLVE_PATH) as MockResolve,
            patch(
                "openhands.agent_server.conversation_service.is_tool_usable",
                return_value=False,
            ),
        ):
            store_inst = MockStore.return_value
            store_inst.name_for_id.return_value = profile.name
            store_inst.load.return_value = profile
            mock_config = MagicMock()
            mock_config.create_agent.return_value = agent
            MockResolve.return_value = mock_config

            result_agent, _ = _resolve_agent_from_profile(
                profile.id, cipher=None, mcp_config={}
            )

        assert result_agent is agent

    def test_openhands_explicit_tools_never_amended(self):
        """An explicit profile tools list ([] included) is authoritative: the
        serving layer must not inject browser on top of it."""
        from openhands.agent_server.conversation_service import (
            _resolve_agent_from_profile,
        )

        profile = _make_openhands_profile().model_copy(update={"tools": []})
        agent = _make_agent()

        with (
            patch(_STORE_PATH) as MockStore,
            patch(_LLM_STORE_PATH),
            patch(_RESOLVE_PATH) as MockResolve,
            patch(
                "openhands.agent_server.conversation_service.is_tool_usable",
                return_value=True,
            ) as MockUsable,
        ):
            store_inst = MockStore.return_value
            store_inst.name_for_id.return_value = profile.name
            store_inst.load.return_value = profile
            mock_config = MagicMock()
            mock_config.create_agent.return_value = agent
            MockResolve.return_value = mock_config

            result_agent, _ = _resolve_agent_from_profile(
                profile.id, cipher=None, mcp_config={}
            )

        MockUsable.assert_not_called()
        assert result_agent is agent

    def test_acp_profile_never_gets_browser_injection(self):
        """ACP agents own their tooling — the injection is OpenHands-only."""
        from openhands.agent_server.conversation_service import (
            _resolve_agent_from_profile,
        )

        profile = _make_acp_profile()
        agent = _make_agent()

        with (
            patch(_STORE_PATH) as MockStore,
            patch(_LLM_STORE_PATH),
            patch(_RESOLVE_PATH) as MockResolve,
            patch(
                "openhands.agent_server.conversation_service.is_tool_usable",
                return_value=True,
            ) as MockUsable,
        ):
            store_inst = MockStore.return_value
            store_inst.name_for_id.return_value = profile.name
            store_inst.load.return_value = profile
            mock_config = MagicMock()
            mock_config.create_agent.return_value = agent
            MockResolve.return_value = mock_config

            result_agent, _ = _resolve_agent_from_profile(
                profile.id, cipher=None, mcp_config={}
            )

        MockUsable.assert_not_called()
        assert result_agent is agent

    def test_openhands_default_profile_triggers_discovery(self):
        """An OpenHands profile always discovers the skill catalog (the deny-list
        needs the full set, minus disabled names). The default deny-list is []
        (all discovered); there is no discovery-skip path anymore (#4017)."""
        from openhands.agent_server.conversation_service import (
            _resolve_agent_from_profile,
        )

        profile = _make_openhands_profile()
        assert profile.disabled_skills == []  # the default: disable nothing
        agent = _make_agent()

        with (
            patch(_STORE_PATH) as MockStore,
            patch(_LLM_STORE_PATH),
            patch(_RESOLVE_PATH) as MockResolve,
            patch(_DISCOVER_PATH, return_value=[]) as MockDiscover,
        ):
            store_inst = MockStore.return_value
            store_inst.name_for_id.return_value = profile.name
            store_inst.load.return_value = profile
            mock_config = MagicMock()
            mock_config.create_agent.return_value = agent
            MockResolve.return_value = mock_config

            _resolve_agent_from_profile(profile.id, cipher=None, mcp_config={})

        MockDiscover.assert_called_once()

    def test_dangling_mcp_server_ref_propagates(self):
        from openhands.agent_server.conversation_service import (
            _resolve_agent_from_profile,
        )

        profile = _make_openhands_profile()
        with (
            patch(_STORE_PATH) as MockStore,
            patch(_LLM_STORE_PATH),
            patch(_RESOLVE_PATH) as MockResolve,
            patch(_DISCOVER_PATH, return_value=[]),
        ):
            store_inst = MockStore.return_value
            store_inst.name_for_id.return_value = profile.name
            store_inst.load.return_value = profile
            MockResolve.side_effect = DanglingMcpServerRef(["missing-server"])

            with pytest.raises(DanglingMcpServerRef) as exc_info:
                _resolve_agent_from_profile(profile.id, cipher=None, mcp_config={})
        assert "missing-server" in exc_info.value.missing

    def test_acp_profile_resolves_to_acp_agent(self):
        from openhands.agent_server.conversation_service import (
            _resolve_agent_from_profile,
        )
        from openhands.sdk.agent.acp_agent import ACPAgent

        # ACP profiles carry no user/public skills, so discovery never runs and
        # the resolver receives available_skills=None.
        profile = _make_acp_profile()
        acp_agent = MagicMock(spec=ACPAgent)

        with (
            patch(_STORE_PATH) as MockStore,
            patch(_LLM_STORE_PATH),
            patch(_RESOLVE_PATH) as MockResolve,
            patch(_DISCOVER_PATH) as MockDiscover,
        ):
            store_inst = MockStore.return_value
            store_inst.name_for_id.return_value = profile.name
            store_inst.load.return_value = profile
            mock_config = MagicMock()
            mock_config.create_agent.return_value = acp_agent
            MockResolve.return_value = mock_config

            result_agent, launched = _resolve_agent_from_profile(
                profile.id, cipher=None, mcp_config={}
            )

        assert result_agent is acp_agent
        assert launched.agent_profile_id == profile.id
        assert launched.revision == profile.revision
        MockDiscover.assert_not_called()
        assert MockResolve.call_args.kwargs["available_skills"] is None


# ---------------------------------------------------------------------------
# Service-layer: conversation start with agent_profile_id
# ---------------------------------------------------------------------------


class TestConversationServiceStartFromProfile:
    @pytest.mark.asyncio
    async def test_start_from_profile_stamps_launched_agent_profile_on_stored(
        self, tmp_path
    ):
        """_start_conversation passes launched_agent_profile to StoredConversation."""
        profile_id = uuid4()
        agent = _make_agent()
        launched_agent_profile = LaunchedAgentProfile(
            agent_profile_id=profile_id, revision=5
        )
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
            return_value=(agent, launched_agent_profile),
        ):
            service = ConversationService(conversations_dir=tmp_path)
            service._event_services = {}

            with patch.object(
                service, "_start_event_service", new_callable=AsyncMock
            ) as mock_ses:
                mock_es = AsyncMock(spec=EventService)
                mock_es.get_state.return_value = mock_state
                mock_es.stored = MagicMock(
                    launched_agent_profile=launched_agent_profile,
                    client_tools=[],
                    title=None,
                    metrics=None,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    forked_from_conversation_id=None,
                    forked_from_event_id=None,
                    parent_conversation_id=None,
                )

                async def capture_start(stored):
                    captured["stored"] = stored
                    return mock_es

                mock_ses.side_effect = capture_start

                info, is_new = await service.start_conversation(request)

        stored = captured.get("stored")
        assert stored is not None, "StoredConversation was not captured"
        assert stored.launched_agent_profile is not None
        assert stored.launched_agent_profile.agent_profile_id == profile_id
        assert stored.launched_agent_profile.revision == 5
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

    # No dangling-skill 422: skills use a deny-list (disabled_skills) that can't
    # dangle — a disabled name absent from the catalog is a no-op, so a profile
    # launch never fails on skill selection (#4017).


# ---------------------------------------------------------------------------
# Provenance round-trip: LaunchedAgentProfile survives serialization
# ---------------------------------------------------------------------------


class TestLaunchedAgentProfileRoundTrip:
    def test_launched_agent_profile_survives_stored_conversation_round_trip(self):
        """LaunchedAgentProfile survives model_dump/model_validate round-trip."""
        profile_id = uuid4()
        lp = LaunchedAgentProfile(agent_profile_id=profile_id, revision=7)
        stored = StoredConversation(
            id=uuid4(),
            agent=_make_agent(),
            workspace=LocalWorkspace(working_dir="/tmp"),
            launched_agent_profile=lp,
        )

        dumped = stored.model_dump(mode="json")
        assert dumped["launched_agent_profile"] is not None
        assert dumped["launched_agent_profile"]["agent_profile_id"] == str(profile_id)
        assert dumped["launched_agent_profile"]["revision"] == 7

        reloaded = StoredConversation.model_validate({"id": str(stored.id), **dumped})
        assert reloaded.launched_agent_profile is not None
        assert reloaded.launched_agent_profile.agent_profile_id == profile_id
        assert reloaded.launched_agent_profile.revision == 7

    def test_stored_conversation_without_profile_has_none(self):
        stored = StoredConversation(
            id=uuid4(),
            agent=_make_agent(),
            workspace=LocalWorkspace(working_dir="/tmp"),
        )
        assert stored.launched_agent_profile is None

    def test_agent_profile_id_excluded_from_stored_conversation_persistence(self):
        """Regression: agent_profile_id must NOT appear in StoredConversation payload.

        StartConversationRequest.model_dump() includes agent_profile_id for HTTP
        transport.  _start_conversation excludes it before building StoredConversation
        (the field is resolved into launched_agent_profile); this test verifies that a
        StoredConversation built from a resolved request contains neither the raw
        profile UUID nor re-exposes it.
        """
        profile_id = uuid4()
        # Simulate the resolved state: agent is set, agent_profile_id excluded.
        request = StartConversationRequest(
            agent_profile_id=profile_id,
            workspace=LocalWorkspace(working_dir="/tmp"),
        )
        # Mirror what _start_conversation does: exclude agent_profile_id from
        # the persistence payload before constructing StoredConversation.
        request_data = request.model_dump(mode="json", exclude={"agent_profile_id"})
        agent = _make_agent()
        request_data["agent"] = agent.model_dump(mode="json")
        stored = StoredConversation(id=uuid4(), **request_data)
        dumped = stored.model_dump(mode="json")
        assert "agent_profile_id" not in dumped

    def test_launched_agent_profile_in_conversation_info(self):
        profile_id = uuid4()
        lp = LaunchedAgentProfile(agent_profile_id=profile_id, revision=3)
        now = datetime.now(UTC)
        info = ConversationInfo(
            id=uuid4(),
            agent=_make_agent(),
            workspace=LocalWorkspace(working_dir="/tmp"),
            execution_status=ConversationExecutionStatus.IDLE,
            created_at=now,
            updated_at=now,
            launched_agent_profile=lp,
        )
        assert info.launched_agent_profile is not None
        assert info.launched_agent_profile.agent_profile_id == profile_id
        assert info.launched_agent_profile.revision == 3

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
        assert info.launched_agent_profile is None

    def test_launched_agent_profile_survives_json_serialization(self, tmp_path):
        """Simulate meta.json round-trip: dump → write → read → validate."""
        profile_id = uuid4()
        lp = LaunchedAgentProfile(agent_profile_id=profile_id, revision=5)
        stored = StoredConversation(
            id=uuid4(),
            agent=_make_agent(),
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
            launched_agent_profile=lp,
        )
        meta_file = tmp_path / "meta.json"
        meta_file.write_text(stored.model_dump_json())

        reloaded = StoredConversation.model_validate_json(meta_file.read_text())
        assert reloaded.launched_agent_profile is not None
        assert reloaded.launched_agent_profile.agent_profile_id == profile_id
        assert reloaded.launched_agent_profile.revision == 5
