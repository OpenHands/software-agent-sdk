"""Conversation start from each agent source, through resolve and finalize.

Covers:
- the agent-source rules of ``StartConversationRequest``
- profile, agent_settings and raw-agent launches in the service
- unknown-id 404 / dangling-ref 422 / store 5xx (router layer)
- LaunchedAgentProfile provenance round-trip through StoredConversation
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

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
from openhands.agent_server.persistence import PersistedSettings
from openhands.sdk import LLM, Agent, AgentBase, AgentContext
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.launch import (
    LaunchedAgent,
    LaunchStoreError,
    LaunchStores,
    UnresolvedProfileReferences,
)
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.profiles.agent_profile import (
    ACPAgentProfile,
    OpenHandsAgentProfile,
)
from openhands.sdk.profiles.resolver import ProfileNotFound
from openhands.sdk.secret import StaticSecret
from openhands.sdk.settings.model import ACPAgentSettings, OpenHandsAgentSettings
from openhands.sdk.skills import Skill
from openhands.sdk.tool import Tool
from openhands.sdk.workspace import LocalWorkspace
from tests.sdk.launch import fakes


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


def _make_openhands_profile(**kwargs) -> OpenHandsAgentProfile:
    return OpenHandsAgentProfile(
        name="my-profile", revision=3, llm_profile_ref="default", **kwargs
    )


def _make_acp_profile(**kwargs) -> ACPAgentProfile:
    return ACPAgentProfile(
        name="acp-profile", revision=1, acp_server="claude-code", **kwargs
    )


def _make_agent() -> Agent:
    return Agent(llm=LLM(model="gpt-4o", usage_id="llm"), tools=[])


_STORES_PATH = "openhands.agent_server.conversation_service.server_launch_stores"
_SETTINGS_STORE_PATH = "openhands.agent_server.persistence.get_settings_store"
_BROWSER_PATH = "openhands.agent_server.launch.is_tool_usable"


def _event_service_for(stored: StoredConversation, agent: AgentBase) -> AsyncMock:
    event_service = AsyncMock(spec=EventService)
    event_service.get_state.return_value = ConversationState(
        id=stored.id,
        agent=agent,
        workspace=stored.workspace,
        execution_status=ConversationExecutionStatus.IDLE,
    )
    event_service.stored = MagicMock(
        launched_agent_profile=stored.launched_agent_profile,
        client_tools=[],
        title=None,
        metrics=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        forked_from_conversation_id=None,
        forked_from_event_id=None,
        parent_conversation_id=None,
    )
    return event_service


async def _start(
    tmp_path,
    request: StartConversationRequest,
    *,
    stores: LaunchStores | None = None,
    persisted: PersistedSettings | None = None,
    browser: bool = False,
) -> tuple[StoredConversation, LaunchedAgent]:
    """Start ``request`` through the real pipeline; return what reached the service."""
    captured: dict[str, Any] = {}

    async def capture_start(stored, **kwargs):
        captured["stored"] = stored
        captured["launched"] = kwargs["launched"]
        return _event_service_for(stored, kwargs["launched"].agent)

    service = ConversationService(conversations_dir=tmp_path / "conversations")
    service._event_services = {}
    with (
        patch(_SETTINGS_STORE_PATH) as settings_store,
        patch(_STORES_PATH, return_value=stores or fakes.stores()),
        patch(_BROWSER_PATH, return_value=browser),
        patch.object(
            service,
            "_start_event_service",
            new_callable=AsyncMock,
            side_effect=capture_start,
        ),
    ):
        settings_store.return_value.load.return_value = persisted or PersistedSettings()
        await service.start_conversation(request)
    return captured["stored"], captured["launched"]


def _profile_request(tmp_path, profile, **kwargs) -> StartConversationRequest:
    return StartConversationRequest(
        agent_profile_id=profile.id,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
        **kwargs,
    )


def _memory_on() -> PersistedSettings:
    return PersistedSettings(
        agent_settings=OpenHandsAgentSettings(
            agent_context=AgentContext(load_memory=True)
        )
    )


_MEMORY_OFF = [
    pytest.param(
        PersistedSettings(
            agent_settings=OpenHandsAgentSettings(agent_context=AgentContext())
        ),
        id="preference-off",
    ),
    # An ACP-kind settings record leaves ``agent_context`` null until something
    # writes to it, so the read must tolerate its absence.
    pytest.param(
        PersistedSettings(agent_settings=ACPAgentSettings()),
        id="stored-settings-without-agent-context",
    ),
]


# ---------------------------------------------------------------------------
# SDK-layer: agent sources (StartConversationRequest)
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

    def test_agent_settings_are_carried_to_the_server_unbuilt(self):
        req = StartConversationRequest(
            agent_settings={"agent_kind": "openhands", "llm": {"model": "gpt-4o"}},
            workspace=LocalWorkspace(working_dir="/tmp"),
        )
        assert req.agent is None
        assert req.agent_settings is not None

    def test_invalid_agent_settings_are_rejected_early(self):
        with pytest.raises(ValidationError):
            StartConversationRequest(
                agent_settings={"agent_kind": "not-a-kind"},
                workspace=LocalWorkspace(working_dir="/tmp"),
            )

    @pytest.mark.parametrize(
        "sources",
        [
            {"agent_profile_id": str(uuid4()), "agent": _make_agent()},
            {
                "agent_profile_id": str(uuid4()),
                "agent_settings": {"agent_kind": "openhands"},
            },
        ],
    )
    def test_profile_sources_are_mutually_exclusive(self, sources):
        with pytest.raises(ValidationError, match="mutually exclusive"):
            StartConversationRequest(
                workspace=LocalWorkspace(working_dir="/tmp"), **sources
            )

    def test_an_explicit_null_is_not_a_source(self):
        req = StartConversationRequest.model_validate(
            {
                "agent_profile_id": str(uuid4()),
                "agent": None,
                "agent_settings": None,
                "workspace": {"working_dir": "/tmp"},
            }
        )
        assert req.agent is None

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
# Service-layer: profile launches
# ---------------------------------------------------------------------------


class TestProfileLaunch:
    @pytest.mark.asyncio
    async def test_a_profile_launch_records_its_provenance(self, tmp_path):
        profile = _make_openhands_profile(secret_refs=["GITHUB_TOKEN"])

        stored, launched = await _start(
            tmp_path, _profile_request(tmp_path, profile), stores=fakes.stores(profile)
        )

        assert stored.launched_agent_profile is not None
        assert stored.launched_agent_profile.agent_profile_id == profile.id
        assert stored.launched_agent_profile.revision == 3
        assert stored.launched_agent_profile.secret_refs == ["GITHUB_TOKEN"]
        assert launched.profile == stored.launched_agent_profile

    @pytest.mark.asyncio
    async def test_an_unknown_profile_is_not_found(self, tmp_path):
        request = _profile_request(tmp_path, _make_openhands_profile())

        with pytest.raises(ProfileNotFound):
            await _start(tmp_path, request, stores=fakes.stores())

    @pytest.mark.asyncio
    async def test_dangling_references_fail_the_launch_together(self, tmp_path):
        profile = OpenHandsAgentProfile(
            name="p", llm_profile_ref="missing", mcp_server_refs=["mcp-server-x"]
        )

        with pytest.raises(UnresolvedProfileReferences) as exc_info:
            await _start(
                tmp_path,
                _profile_request(tmp_path, profile),
                stores=fakes.stores(profile),
            )

        assert exc_info.value.llm_profile_ref == "missing"
        assert exc_info.value.mcp_server_refs == ["mcp-server-x"]

    @pytest.mark.asyncio
    async def test_the_profile_llm_streams(self, tmp_path):
        """A client cannot set llm.stream on a profile's referenced LLM (#4014)."""
        profile = _make_openhands_profile()
        stored_llm = fakes.llm(stream=False)

        _, launched = await _start(
            tmp_path,
            _profile_request(tmp_path, profile),
            stores=fakes.stores(profile, llms={"default": stored_llm}),
        )

        assert isinstance(launched.agent, Agent)
        assert launched.agent.llm.stream is True
        assert stored_llm.stream is False

    @pytest.mark.asyncio
    async def test_the_profile_persona_reaches_the_launched_agent(self, tmp_path):
        persona = "You only answer questions about this repository."
        profile = _make_openhands_profile(persona=persona)

        _, launched = await _start(
            tmp_path, _profile_request(tmp_path, profile), stores=fakes.stores(profile)
        )

        assert isinstance(launched.agent, Agent)
        assert launched.agent.persona == persona
        assert launched.agent.static_system_message.startswith(persona)
        assert "<SECURITY>" in launched.agent.static_system_message

    @pytest.mark.parametrize(
        ("browser", "expected"),
        [
            (True, {"terminal", "file_editor", "task_tracker", "browser_tool_set"}),
            (False, {"terminal", "file_editor", "task_tracker"}),
        ],
    )
    @pytest.mark.asyncio
    async def test_default_tools_get_browser_when_this_runtime_can_run_it(
        self, tmp_path, browser, expected
    ):
        profile = _make_openhands_profile()

        _, launched = await _start(
            tmp_path,
            _profile_request(tmp_path, profile),
            stores=fakes.stores(profile),
            browser=browser,
        )

        assert {tool.name for tool in launched.agent.tools} == expected

    @pytest.mark.asyncio
    async def test_an_explicit_tool_list_is_never_amended(self, tmp_path):
        profile = _make_openhands_profile(tools=[Tool(name="terminal")])

        _, launched = await _start(
            tmp_path,
            _profile_request(tmp_path, profile),
            stores=fakes.stores(profile),
            browser=True,
        )

        assert [tool.name for tool in launched.agent.tools] == ["terminal"]

    @pytest.mark.asyncio
    async def test_an_acp_profile_launches_an_acp_agent_without_browser(self, tmp_path):
        profile = _make_acp_profile()

        _, launched = await _start(
            tmp_path,
            _profile_request(tmp_path, profile),
            stores=fakes.stores(profile),
            browser=True,
        )

        assert isinstance(launched.agent, ACPAgent)
        assert launched.agent.tools == []

    @pytest.mark.parametrize(
        ("sourcing", "expected"),
        [("native", []), ("openhands_managed", ["managed"])],
    )
    @pytest.mark.asyncio
    async def test_this_runtime_decides_the_acp_skill_catalog(
        self, tmp_path, sourcing, expected
    ):
        """The catalog is always resolved; the runtime's sourcing keeps or drops it."""
        profile = _make_acp_profile()
        stores = fakes.stores(profile, skills=[Skill(name="managed", content="x")])
        request = _profile_request(tmp_path, profile)
        captured: dict[str, Any] = {}

        async def capture_start(stored, **kwargs):
            captured["agent"] = kwargs["launched"].agent
            return _event_service_for(stored, kwargs["launched"].agent)

        service = ConversationService(
            conversations_dir=tmp_path / "conversations", acp_skill_sourcing=sourcing
        )
        service._event_services = {}
        with (
            patch(_SETTINGS_STORE_PATH) as settings_store,
            patch(_STORES_PATH, return_value=stores),
            patch.object(service, "_start_event_service", side_effect=capture_start),
        ):
            settings_store.return_value.load.return_value = PersistedSettings()
            await service.start_conversation(request)

        context = captured["agent"].agent_context
        assert context is not None
        assert [skill.name for skill in context.skills] == expected

    @pytest.mark.asyncio
    async def test_a_profile_launch_has_a_fresh_timestamp(self, tmp_path):
        profile = _make_openhands_profile()
        before = datetime.now(UTC)

        _, launched = await _start(
            tmp_path, _profile_request(tmp_path, profile), stores=fakes.stores(profile)
        )

        context = launched.agent.agent_context
        assert context is not None
        assert isinstance(context.current_datetime, datetime)
        assert context.current_datetime >= before

    @pytest.mark.parametrize("agent_kind", ["openhands", "acp"])
    @pytest.mark.asyncio
    async def test_a_profile_launch_inherits_the_memory_preference(
        self, tmp_path, agent_kind
    ):
        """Persistent memory is a global preference the profile cannot carry."""
        profile = (
            _make_acp_profile() if agent_kind == "acp" else _make_openhands_profile()
        )

        _, launched = await _start(
            tmp_path,
            _profile_request(tmp_path, profile),
            stores=fakes.stores(profile),
            persisted=_memory_on(),
        )

        assert launched.agent.agent_context is not None
        assert launched.agent.agent_context.load_memory is True

    @pytest.mark.parametrize("persisted", _MEMORY_OFF)
    @pytest.mark.asyncio
    async def test_a_profile_launch_leaves_memory_off_without_the_preference(
        self, tmp_path, persisted
    ):
        profile = _make_openhands_profile()

        _, launched = await _start(
            tmp_path,
            _profile_request(tmp_path, profile),
            stores=fakes.stores(profile),
            persisted=persisted,
        )

        assert launched.agent.agent_context is not None
        assert launched.agent.agent_context.load_memory is False


# ---------------------------------------------------------------------------
# Service-layer: agent_settings and raw agent launches
# ---------------------------------------------------------------------------


def _no_stores() -> LaunchStores:
    raise AssertionError("an agent or agent_settings launch reads no store")


async def _start_with_agent(
    tmp_path,
    persisted: PersistedSettings,
    *,
    agent: Agent | None = None,
    agent_settings: dict[str, Any] | None = None,
    browser: bool = False,
) -> AgentBase:
    request = StartConversationRequest(
        agent=cast(AgentBase, agent),
        agent_settings=agent_settings,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
    )
    with patch(_STORES_PATH, side_effect=_no_stores):
        _, launched = await _start(
            tmp_path, request, persisted=persisted, browser=browser
        )
    return launched.agent


_SETTINGS_PAYLOAD = {
    "agent_kind": "openhands",
    "llm": {"model": "gpt-4o", "usage_id": "llm"},
}


class TestDirectAgentLaunch:
    @pytest.mark.asyncio
    async def test_a_raw_agent_launch_inherits_the_memory_preference(self, tmp_path):
        agent = await _start_with_agent(tmp_path, _memory_on(), agent=_make_agent())

        assert agent.agent_context is not None
        assert agent.agent_context.load_memory is True

    @pytest.mark.parametrize("persisted", _MEMORY_OFF)
    @pytest.mark.asyncio
    async def test_a_raw_agent_launch_leaves_memory_off_without_the_preference(
        self, tmp_path, persisted
    ):
        agent = await _start_with_agent(tmp_path, persisted, agent=_make_agent())

        context = agent.agent_context
        assert not (context is not None and context.load_memory)

    @pytest.mark.asyncio
    async def test_an_agent_settings_launch_inherits_the_memory_preference(
        self, tmp_path
    ):
        agent = await _start_with_agent(
            tmp_path, _memory_on(), agent_settings=_SETTINGS_PAYLOAD
        )

        assert agent.agent_context is not None
        assert agent.agent_context.load_memory is True

    @pytest.mark.parametrize("persisted", _MEMORY_OFF)
    @pytest.mark.asyncio
    async def test_an_agent_settings_launch_leaves_memory_off_without_the_preference(
        self, tmp_path, persisted
    ):
        agent = await _start_with_agent(
            tmp_path, persisted, agent_settings=_SETTINGS_PAYLOAD
        )

        assert agent.agent_context is not None
        assert agent.agent_context.load_memory is False

    @pytest.mark.asyncio
    async def test_the_memory_stamp_keeps_the_rest_of_the_context(self, tmp_path):
        agent_with_context = Agent(
            llm=LLM(model="gpt-4o", usage_id="llm"),
            tools=[],
            agent_context=AgentContext(
                load_memory=True, skills=[], system_message_suffix="client-set suffix"
            ),
        )

        agent = await _start_with_agent(
            tmp_path, _memory_on(), agent=agent_with_context
        )

        assert agent.agent_context is not None
        assert agent.agent_context.load_memory is True
        assert agent.agent_context.system_message_suffix == "client-set suffix"

    @pytest.mark.asyncio
    async def test_the_memory_stamp_does_not_introduce_a_timestamp(self, tmp_path):
        agent = await _start_with_agent(tmp_path, _memory_on(), agent=_make_agent())

        assert agent.agent_context is not None
        assert agent.agent_context.current_datetime is None

    @pytest.mark.asyncio
    async def test_an_agent_settings_launch_gets_a_fresh_timestamp(self, tmp_path):
        stale = "2026-09-30T11:54:29+02:00"
        before = datetime.now(UTC)

        agent = await _start_with_agent(
            tmp_path,
            PersistedSettings(),
            agent_settings={
                **_SETTINGS_PAYLOAD,
                "agent_context": {"current_datetime": stale},
            },
        )

        context = agent.agent_context
        assert context is not None
        assert isinstance(context.current_datetime, datetime)
        assert context.current_datetime >= before

    @pytest.mark.asyncio
    async def test_agent_settings_without_tools_get_the_runtime_default_set(
        self, tmp_path
    ):
        agent = await _start_with_agent(
            tmp_path,
            PersistedSettings(),
            agent_settings=_SETTINGS_PAYLOAD,
            browser=True,
        )

        assert "browser_tool_set" in {tool.name for tool in agent.tools}


# ---------------------------------------------------------------------------
# Router-layer: HTTP error mapping
# ---------------------------------------------------------------------------


def _post_profile_launch(client, mock_conversation_service, error):
    mock_conversation_service.start_conversation.side_effect = error
    client.app.dependency_overrides[get_conversation_service] = lambda: (
        mock_conversation_service
    )
    return client.post(
        "/api/conversations",
        json={
            "agent_profile_id": str(uuid4()),
            "workspace": {"working_dir": "/tmp/test", "kind": "LocalWorkspace"},
        },
    )


class TestConversationRouterProfileErrors:
    def test_profile_not_found_returns_404(self, client, mock_conversation_service):
        resp = _post_profile_launch(
            client,
            mock_conversation_service,
            ProfileNotFound("Agent profile with id 'abc' not found"),
        )
        assert resp.status_code == 404
        assert "not found" in resp.json().get("detail", "").lower()

    def test_dangling_references_return_one_structured_422(
        self, client, mock_conversation_service
    ):
        resp = _post_profile_launch(
            client,
            mock_conversation_service,
            UnresolvedProfileReferences(
                llm_profile_ref="gone",
                mcp_server_refs=["missing-server", "another-missing"],
            ),
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "unresolved_profile_references"
        assert detail["dangling_llm_profile_ref"] == "gone"
        assert detail["dangling_mcp_server_refs"] == [
            "missing-server",
            "another-missing",
        ]

    @pytest.mark.parametrize(("retryable", "status"), [(True, 503), (False, 500)])
    def test_an_unreadable_store_is_a_server_error(
        self, client, mock_conversation_service, retryable, status
    ):
        resp = _post_profile_launch(
            client,
            mock_conversation_service,
            LaunchStoreError("store unavailable", retryable=retryable),
        )
        assert resp.status_code == status

    # No dangling-skill 422: skills use a deny-list (disabled_skills) that can't
    # dangle — a disabled name absent from the catalog is a no-op (#4017).


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
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
            launched_agent_profile=lp,
        )
        meta_file = tmp_path / "meta.json"
        meta_file.write_text(stored.model_dump_json())

        reloaded = StoredConversation.model_validate_json(meta_file.read_text())
        assert reloaded.launched_agent_profile is not None
        assert reloaded.launched_agent_profile.agent_profile_id == profile_id
        assert reloaded.launched_agent_profile.revision == 5


class TestProfileSecretScope:
    """``secret_refs`` narrows a launch's secrets, enforced server-side (#17236)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("secret_refs", "expected"),
        [
            (None, {"GITHUB_TOKEN", "DATADOG_API_KEY"}),
            ([], set()),
            (["GITHUB_TOKEN"], {"GITHUB_TOKEN"}),
            (["GITHUB_TOKEN", "MISSING"], {"GITHUB_TOKEN"}),
            (["MISSING"], set()),
        ],
    )
    async def test_start_conversation_drops_secrets_the_profile_disallows(
        self, tmp_path, secret_refs, expected
    ):
        """The filter runs on the request, so a client cannot widen the scope."""
        profile = _make_openhands_profile(secret_refs=secret_refs)
        request = _profile_request(
            tmp_path,
            profile,
            secrets={
                "GITHUB_TOKEN": StaticSecret(value=SecretStr("gh")),
                "DATADOG_API_KEY": StaticSecret(value=SecretStr("dd")),
            },
        )

        stored, _ = await _start(tmp_path, request, stores=fakes.stores(profile))

        assert set(stored.secrets) == expected

    @pytest.mark.asyncio
    async def test_a_scoped_acp_profile_gets_no_implicit_provider_credentials(
        self, tmp_path
    ):
        # Strict: an ACP profile must list its own credential to receive it.
        profile = _make_acp_profile(secret_refs=[])
        request = _profile_request(
            tmp_path,
            profile,
            secrets={"ANTHROPIC_API_KEY": StaticSecret(value=SecretStr("sk"))},
        )

        stored, _ = await _start(tmp_path, request, stores=fakes.stores(profile))

        assert stored.secrets == {}

    @pytest.mark.asyncio
    async def test_mcp_servers_outside_the_profile_scope_are_not_attached(
        self, tmp_path
    ):
        profile = _make_openhands_profile(mcp_server_refs=["kept"])
        stores = fakes.stores(
            profile,
            mcp={
                "kept": MCPServer(command="echo"),
                "dropped": MCPServer(command="echo"),
            },
        )

        _, launched = await _start(
            tmp_path, _profile_request(tmp_path, profile), stores=stores
        )

        assert isinstance(launched.agent, Agent)
        assert list(launched.agent.mcp_config) == ["kept"]


@pytest.mark.asyncio
async def test_only_a_launched_agent_starts_a_new_conversation(tmp_path):
    service = ConversationService(conversations_dir=tmp_path / "conversations")
    service._event_services = {}
    stored = StoredConversation(
        id=uuid4(), workspace=LocalWorkspace(working_dir=str(tmp_path))
    )

    with pytest.raises(ValueError, match="without an agent"):
        await service._start_event_service(stored, persisted_agent=_make_agent())
