"""Tests for launching a conversation from an Agent Profile.

Covers:
- the server launch pipeline (``agent_launch``) for the OpenHands + ACP paths
- mutual-exclusivity validation (SDK layer)
- unknown-id 404 / dangling-ref 422 (router layer)
- LaunchedAgentProfile provenance round-trip through StoredConversation
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from openhands.agent_server.agent_launch import (
    load_stored_profile,
    prepare_launch_request,
)
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
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_agent_profile_store,
    get_llm_profile_store,
)
from openhands.sdk import LLM, Agent, AgentBase, AgentContext
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.mcp.config import coerce_mcp_config
from openhands.sdk.profiles import (
    AgentLaunchPlan,
    AgentLaunchRuntime,
    ProfileNotFound,
    UnresolvedProfileReferences,
)
from openhands.sdk.profiles.agent_profile import (
    ACPAgentProfile,
    OpenHandsAgentProfile,
)
from openhands.sdk.secret import StaticSecret
from openhands.sdk.settings.model import ACPAgentSettings, OpenHandsAgentSettings
from openhands.sdk.skills import Skill
from openhands.sdk.tool import BROWSER_TOOL_NAME, Tool
from openhands.sdk.workspace import LocalWorkspace


# The launch pipeline binds these names in ``agent_launch``.
_DISCOVER_PATH = "openhands.agent_server.agent_launch.discover_profile_skills"
_BROWSER_PROBE_PATH = "openhands.agent_server.agent_launch.is_tool_usable"
# The settings read that feeds the launch runtime resolves through the
# package-level name ``conversation_service`` imports inside the function body.
_SETTINGS_STORE_PATH = "openhands.agent_server.persistence.get_settings_store"

LLM_PROFILE_REF = "default"


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


def _store_llm_profile(name: str = LLM_PROFILE_REF, **updates) -> LLM:
    llm = LLM(model="gpt-4o", usage_id="agent", api_key=SecretStr("llm-key"), **updates)
    get_llm_profile_store().save(name, llm, include_secrets=True)
    return llm


def _make_openhands_profile(profile_id: UUID | None = None) -> OpenHandsAgentProfile:
    return OpenHandsAgentProfile(
        id=profile_id or uuid4(),
        name="my-profile",
        revision=3,
        llm_profile_ref=LLM_PROFILE_REF,
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


def _runtime(**updates) -> AgentLaunchRuntime:
    """A pinned launch runtime: no host probing, streaming forced as a server does."""
    return AgentLaunchRuntime(
        **{
            "browser_available": False,
            "acp_skill_sourcing": "native",
            "stream": True,
            **updates,
        }
    )


def _skill(name: str) -> Skill:
    return Skill(name=name, content=f"{name} content")


def _launch(
    profile: OpenHandsAgentProfile | ACPAgentProfile,
    *,
    runtime: AgentLaunchRuntime | None = None,
    settings: PersistedSettings | None = None,
    skills: list[Skill] | None = None,
    secrets: dict[str, Any] | None = None,
    discover: MagicMock | None = None,
) -> tuple[StartConversationRequest, AgentLaunchPlan]:
    """Store ``profile`` and run the server launch pipeline against it."""
    _store_llm_profile()
    get_agent_profile_store().save(profile)
    request = StartConversationRequest(
        agent_profile_id=profile.id,
        workspace=LocalWorkspace(working_dir="/tmp"),
        secrets=secrets or {},
    )
    discovery = discover or MagicMock(return_value=skills or [])
    with patch(_DISCOVER_PATH, discovery):
        return prepare_launch_request(
            request,
            cipher=None,
            settings=settings or PersistedSettings(),
            runtime=runtime or _runtime(),
        )


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

    def test_an_explicit_null_source_is_not_a_source(self):
        """Clients round-trip the whole family, sending null for the unused keys."""
        req = StartConversationRequest.model_validate(
            {
                "agent": None,
                "agent_profile": None,
                "agent_profile_id": str(uuid4()),
                "workspace": {"working_dir": "/tmp"},
            }
        )
        assert req.agent_profile_id is not None
        assert req.agent is None

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
# Server launch pipeline: agent_launch
# ---------------------------------------------------------------------------


class TestPrepareLaunchFromProfile:
    def test_unknown_id_raises_profile_not_found(self):
        with pytest.raises(ProfileNotFound, match="not found"):
            load_stored_profile(uuid4())

    def test_openhands_profile_resolves_to_agent_and_stamps_launched(self):
        profile = _make_openhands_profile()

        request, plan = _launch(profile)

        assert isinstance(plan.agent, Agent)
        assert plan.agent.llm.model == "gpt-4o"
        assert request.agent is plan.agent
        assert request.agent_profile_id is None
        assert plan.launched_profile is not None
        assert plan.launched_profile.agent_profile_id == profile.id
        assert plan.launched_profile.revision == profile.revision
        assert plan.launched_profile.inline is False
        assert plan.launched_profile.llm_profile_ref is None

    def test_openhands_profile_keeps_the_catalog_minus_disabled_skills(self):
        """The deny-list needs the whole catalog, not an allow-list (#4017)."""
        profile = _make_openhands_profile().model_copy(
            update={"disabled_skills": ["noisy"]}
        )
        discover = MagicMock(return_value=[_skill("useful"), _skill("noisy")])

        _, plan = _launch(profile, skills=None, discover=discover)

        discover.assert_called_once()
        assert plan.agent is not None
        assert plan.agent.agent_context is not None
        assert [s.name for s in plan.agent.agent_context.skills] == ["useful"]

    def test_openhands_profile_forces_llm_stream_true(self):
        """A client cannot set ``llm.stream`` on a profile's referenced LLM, so the
        server forces it to guarantee on_token wiring (#4014). The stored LLM
        profile is left alone."""
        _store_llm_profile(stream=False)
        profile = _make_openhands_profile()

        _, plan = _launch(profile, runtime=_runtime(stream=True))

        assert plan.agent is not None
        assert plan.agent.llm.stream is True
        assert get_llm_profile_store().load(LLM_PROFILE_REF).stream is False

    def test_openhands_profile_keeps_stream_off_without_the_runtime_flag(self):
        _store_llm_profile(stream=False)
        profile = _make_openhands_profile()

        _, plan = _launch(profile, runtime=_runtime(stream=False))

        assert plan.agent is not None
        assert plan.agent.llm.stream is False

    def test_acp_profile_does_not_force_llm_stream(self):
        """ACP agents stream through the ACP bridge, not an LLM token callback."""
        profile = _make_acp_profile()

        _, plan = _launch(profile, runtime=_runtime(stream=True))

        assert plan.agent is not None
        assert plan.agent.llm.stream is False

    def test_acp_profile_skips_discovery_under_native_sourcing(self):
        """A host-local ACP CLI reads the user's own skills, so none are injected
        and discovery never runs (#4019)."""
        profile = _make_acp_profile()
        discover = MagicMock(return_value=[_skill("managed")])

        _, plan = _launch(
            profile, runtime=_runtime(acp_skill_sourcing="native"), discover=discover
        )

        discover.assert_not_called()
        assert plan.agent is not None
        assert plan.agent.agent_context is not None
        assert plan.agent.agent_context.skills == []

    def test_acp_profile_gets_catalog_under_managed_sourcing(self):
        """In a container the CLI has no host home to read skills from."""
        profile = _make_acp_profile()
        discover = MagicMock(return_value=[_skill("managed")])

        _, plan = _launch(
            profile,
            runtime=_runtime(acp_skill_sourcing="openhands_managed"),
            discover=discover,
        )

        discover.assert_called_once()
        assert plan.agent is not None
        assert plan.agent.agent_context is not None
        assert [s.name for s in plan.agent.agent_context.skills] == ["managed"]

    def test_openhands_default_tools_get_browser_when_usable(self):
        """The serving-layer counterpart of the SDK's deterministic default (#3978)."""
        profile = _make_openhands_profile()
        assert profile.tools is None

        _, plan = _launch(profile, runtime=_runtime(browser_available=True))

        assert plan.agent is not None
        names = [tool.name for tool in plan.agent.tools]
        assert BROWSER_TOOL_NAME in names
        assert "terminal" in names

    def test_openhands_default_tools_skip_browser_when_unusable(self):
        profile = _make_openhands_profile()

        _, plan = _launch(profile, runtime=_runtime(browser_available=False))

        assert plan.agent is not None
        names = [tool.name for tool in plan.agent.tools]
        assert BROWSER_TOOL_NAME not in names
        assert "terminal" in names

    def test_openhands_explicit_tools_never_amended(self):
        """An explicit profile tools list ([] included) is authoritative."""
        profile = _make_openhands_profile().model_copy(update={"tools": []})

        _, plan = _launch(profile, runtime=_runtime(browser_available=True))

        assert plan.agent is not None
        assert plan.agent.tools == []

    def test_openhands_explicit_tool_list_is_used_verbatim(self):
        profile = _make_openhands_profile().model_copy(
            update={"tools": [Tool(name="terminal")]}
        )

        _, plan = _launch(profile, runtime=_runtime(browser_available=True))

        assert plan.agent is not None
        assert [tool.name for tool in plan.agent.tools] == ["terminal"]

    def test_acp_profile_never_gets_browser_injection(self):
        """ACP agents own their tooling — the injection is OpenHands-only."""
        profile = _make_acp_profile()

        _, plan = _launch(profile, runtime=_runtime(browser_available=True))

        assert plan.agent is not None
        assert plan.agent.tools == []

    def test_acp_profile_resolves_to_acp_agent(self):
        from openhands.sdk.agent.acp_agent import ACPAgent

        profile = _make_acp_profile()

        _, plan = _launch(profile)

        assert isinstance(plan.agent, ACPAgent)
        assert plan.agent.acp_server == "claude-code"
        assert plan.launched_profile is not None
        assert plan.launched_profile.agent_profile_id == profile.id
        assert plan.launched_profile.revision == profile.revision

    def test_dangling_mcp_server_ref_raises(self):
        profile = _make_openhands_profile().model_copy(
            update={"mcp_server_refs": ["missing-server"]}
        )

        with pytest.raises(UnresolvedProfileReferences) as exc_info:
            _launch(profile)

        assert exc_info.value.mcp_server_refs == ["missing-server"]
        assert exc_info.value.llm_profile_ref is None

    def test_resolved_mcp_config_is_filtered_to_the_refs(self):
        profile = _make_openhands_profile().model_copy(
            update={"mcp_server_refs": ["kept"]}
        )
        settings = PersistedSettings(
            agent_settings=OpenHandsAgentSettings(
                mcp_config=coerce_mcp_config(
                    {
                        "mcpServers": {
                            "kept": {"command": "echo", "args": ["kept"]},
                            "dropped": {"command": "echo", "args": ["dropped"]},
                        }
                    }
                )
            )
        )

        _, plan = _launch(profile, settings=settings)

        assert plan.agent is not None
        assert list(plan.agent.mcp_config) == ["kept"]

    def test_missing_llm_profile_ref_raises_with_the_ref(self):
        profile = _make_openhands_profile().model_copy(
            update={"llm_profile_ref": "gone"}
        )

        with pytest.raises(UnresolvedProfileReferences) as exc_info:
            _launch(profile)

        assert exc_info.value.llm_profile_ref == "gone"
        assert exc_info.value.mcp_server_refs == []


# ---------------------------------------------------------------------------
# Service-layer: conversation start with agent_profile_id
# ---------------------------------------------------------------------------


def _profile_for(agent_kind: str) -> OpenHandsAgentProfile | ACPAgentProfile:
    return _make_acp_profile() if agent_kind == "acp" else _make_openhands_profile()


def _mock_event_service(state: ConversationState) -> AsyncMock:
    event_service = AsyncMock(spec=EventService)
    event_service.get_state.return_value = state
    event_service.stored = MagicMock(
        launched_agent_profile=None,
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


async def _start_from_profile(
    tmp_path,
    profile: OpenHandsAgentProfile | ACPAgentProfile,
    persisted_settings: PersistedSettings,
) -> tuple[StoredConversation, Any]:
    """Launch from ``profile`` and return the captured ``(StoredConversation, agent)``.

    Only the settings store, skill discovery and the browser probe are stubbed,
    so the launch pipeline runs for real against the profile stores — this is
    the path a client reaches by sending ``agent_profile_id`` alone.
    """
    _store_llm_profile()
    get_agent_profile_store().save(profile)
    request = StartConversationRequest(
        agent_profile_id=profile.id,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
    )
    captured: dict[str, Any] = {}

    async def capture_start(stored, **kwargs):
        agent = kwargs["agent"]
        captured["stored"] = stored
        captured["agent"] = agent
        return _mock_event_service(
            ConversationState(
                id=uuid4(),
                agent=agent,
                workspace=request.workspace,
                execution_status=ConversationExecutionStatus.IDLE,
            )
        )

    service = ConversationService(conversations_dir=tmp_path)
    service._event_services = {}

    with (
        patch(_SETTINGS_STORE_PATH) as MockSettingsStore,
        patch(_DISCOVER_PATH, return_value=[]),
        # Pin the environment probe: browser injection is covered by its own
        # tests above and would otherwise vary with the host.
        patch(_BROWSER_PROBE_PATH, return_value=False),
        patch.object(
            service,
            "_start_event_service",
            new_callable=AsyncMock,
            side_effect=capture_start,
        ),
    ):
        MockSettingsStore.return_value.load.return_value = persisted_settings
        await service.start_conversation(request)

    return captured["stored"], captured["agent"]


async def _start_with_agent(
    tmp_path,
    persisted_settings: PersistedSettings,
    *,
    agent: Agent | None = None,
    agent_settings: dict[str, Any] | None = None,
) -> Any:
    """Launch via a concrete ``agent`` or a raw ``agent_settings`` payload and
    return the captured agent.
    """
    source: dict[str, Any] = (
        {"agent": cast(AgentBase, agent)}
        if agent is not None
        else {"agent_settings": agent_settings}
    )
    request = StartConversationRequest(
        **source,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
    )
    captured: dict[str, Any] = {}

    async def capture_start(stored, **kwargs):
        launched_agent = kwargs["agent"]
        captured["agent"] = launched_agent
        return _mock_event_service(
            ConversationState(
                id=uuid4(),
                agent=launched_agent,
                workspace=request.workspace,
                execution_status=ConversationExecutionStatus.IDLE,
            )
        )

    service = ConversationService(conversations_dir=tmp_path)
    service._event_services = {}

    with (
        patch(_SETTINGS_STORE_PATH) as MockSettingsStore,
        patch(_BROWSER_PROBE_PATH, return_value=False),
        patch.object(
            service,
            "_start_event_service",
            new_callable=AsyncMock,
            side_effect=capture_start,
        ),
    ):
        MockSettingsStore.return_value.load.return_value = persisted_settings
        await service.start_conversation(request)

    return captured["agent"]


class TestConversationServiceStartFromProfile:
    @pytest.mark.asyncio
    async def test_start_from_profile_stamps_launched_agent_profile_on_stored(
        self, tmp_path
    ):
        """_start_conversation passes launched_agent_profile to StoredConversation."""
        profile = _make_openhands_profile()

        stored, agent = await _start_from_profile(
            tmp_path, profile, PersistedSettings()
        )

        assert stored.launched_agent_profile is not None
        assert stored.launched_agent_profile.agent_profile_id == profile.id
        assert stored.launched_agent_profile.revision == profile.revision
        # The resolved agent (not None) must be passed to _start_event_service
        # (it is persisted to base_state.json, not meta.json).
        assert agent is not None
        assert stored.model_dump(mode="json").get("agent_profile_id") is None

    @pytest.mark.asyncio
    async def test_profile_not_found_propagates(self, tmp_path):
        request = StartConversationRequest(
            agent_profile_id=uuid4(),
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
        )
        service = ConversationService(conversations_dir=tmp_path)
        service._event_services = {}

        with pytest.raises(ProfileNotFound):
            await service.start_conversation(request)

    @pytest.mark.asyncio
    async def test_dangling_ref_propagates_from_service(self, tmp_path):
        _store_llm_profile()
        profile = _make_openhands_profile().model_copy(
            update={"mcp_server_refs": ["mcp-server-x"]}
        )
        get_agent_profile_store().save(profile)
        request = StartConversationRequest(
            agent_profile_id=profile.id,
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
        )
        service = ConversationService(conversations_dir=tmp_path)
        service._event_services = {}

        with (
            patch(_DISCOVER_PATH, return_value=[]),
            patch(_BROWSER_PROBE_PATH, return_value=False),
        ):
            with pytest.raises(UnresolvedProfileReferences) as exc_info:
                await service.start_conversation(request)

        assert "mcp-server-x" in exc_info.value.mcp_server_refs

    @pytest.mark.parametrize("agent_kind", ["openhands", "acp"])
    @pytest.mark.asyncio
    async def test_profile_launch_inherits_the_stored_memory_preference(
        self, tmp_path, agent_kind
    ):
        """Persistent memory is a global preference the profile cannot carry.

        A profile launch sends no ``agent_settings``, so without this the
        Settings → Memory toggle would read as enabled while every conversation
        started from a named OpenHands profile or an ACP profile ignored it.
        """
        persisted = PersistedSettings(
            agent_settings=OpenHandsAgentSettings(
                agent_context=AgentContext(load_memory=True)
            )
        )

        _, agent = await _start_from_profile(
            tmp_path, _profile_for(agent_kind), persisted
        )

        assert agent.agent_context is not None
        assert agent.agent_context.load_memory is True

    @pytest.mark.parametrize(
        "persisted_settings",
        [
            pytest.param(
                PersistedSettings(
                    agent_settings=OpenHandsAgentSettings(agent_context=AgentContext())
                ),
                id="preference-off",
            ),
            # An ACP-kind settings record leaves ``agent_context`` null until
            # something writes to it, so the read must tolerate its absence
            # rather than breaking every profile launch.
            pytest.param(
                PersistedSettings(agent_settings=ACPAgentSettings()),
                id="stored-settings-without-agent-context",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_profile_launch_leaves_memory_off_without_the_preference(
        self, tmp_path, persisted_settings
    ):
        _, agent = await _start_from_profile(
            tmp_path, _make_openhands_profile(), persisted_settings
        )

        assert agent.agent_context is not None
        assert agent.agent_context.load_memory is False


class TestConversationServiceStartWithDirectAgent:
    @pytest.mark.asyncio
    async def test_direct_agent_launch_inherits_the_stored_memory_preference(
        self, tmp_path
    ):
        """Same guarantee as the profile launch, for the ``agent`` shape."""
        persisted = PersistedSettings(
            agent_settings=OpenHandsAgentSettings(
                agent_context=AgentContext(load_memory=True)
            )
        )

        agent = await _start_with_agent(tmp_path, persisted, agent=_make_agent())

        assert agent.agent_context is not None
        assert agent.agent_context.load_memory is True

    @pytest.mark.parametrize(
        "persisted_settings",
        [
            pytest.param(
                PersistedSettings(
                    agent_settings=OpenHandsAgentSettings(agent_context=AgentContext())
                ),
                id="preference-off",
            ),
            pytest.param(
                PersistedSettings(agent_settings=ACPAgentSettings()),
                id="stored-settings-without-agent-context",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_direct_agent_launch_leaves_memory_off_without_the_preference(
        self, tmp_path, persisted_settings
    ):
        agent = await _start_with_agent(
            tmp_path, persisted_settings, agent=_make_agent()
        )

        # A directly-constructed Agent has no AgentContext by default, so "off"
        # surfaces as agent_context staying None rather than an explicit
        # AgentContext(load_memory=False) — both mean the same thing to the
        # runtime (see AgentBase's own check: agent_context is not None and
        # agent_context.load_memory).
        effective_load_memory = (
            agent.agent_context is not None and agent.agent_context.load_memory
        )
        assert effective_load_memory is False

    @pytest.mark.asyncio
    async def test_agent_settings_launch_inherits_the_stored_memory_preference(
        self, tmp_path
    ):
        """The deprecated ``agent_settings`` shape needs the same coverage as the
        two shapes that carry an agent or a profile reference."""
        persisted = PersistedSettings(
            agent_settings=OpenHandsAgentSettings(
                agent_context=AgentContext(load_memory=True)
            )
        )

        agent = await _start_with_agent(
            tmp_path,
            persisted,
            agent_settings={
                "agent_kind": "openhands",
                "llm": {"model": "gpt-4o", "usage_id": "llm"},
            },
        )

        assert agent.agent_context is not None
        assert agent.agent_context.load_memory is True

    @pytest.mark.parametrize(
        "persisted_settings",
        [
            pytest.param(
                PersistedSettings(
                    agent_settings=OpenHandsAgentSettings(agent_context=AgentContext())
                ),
                id="preference-off",
            ),
            pytest.param(
                PersistedSettings(agent_settings=ACPAgentSettings()),
                id="stored-settings-without-agent-context",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_agent_settings_launch_leaves_memory_off_without_the_preference(
        self, tmp_path, persisted_settings
    ):
        agent = await _start_with_agent(
            tmp_path,
            persisted_settings,
            agent_settings={
                "agent_kind": "openhands",
                "llm": {"model": "gpt-4o", "usage_id": "llm"},
            },
        )

        assert agent.agent_context is not None
        assert agent.agent_context.load_memory is False

    @pytest.mark.asyncio
    async def test_agent_launch_preserves_context_when_load_memory_already_true(
        self, tmp_path
    ):
        """The stamp must update load_memory in place, not replace the whole
        AgentContext — a client who already opted in keeps every other field
        they set (skills, suffix, etc.) untouched."""
        agent_with_context = Agent(
            llm=LLM(model="gpt-4o", usage_id="llm"),
            tools=[],
            agent_context=AgentContext(
                load_memory=True,
                skills=[],
                system_message_suffix="client-set suffix",
            ),
        )
        persisted = PersistedSettings(
            agent_settings=OpenHandsAgentSettings(
                agent_context=AgentContext(load_memory=True)
            )
        )

        agent = await _start_with_agent(tmp_path, persisted, agent=agent_with_context)

        assert agent.agent_context is not None
        assert agent.agent_context.load_memory is True
        assert agent.agent_context.system_message_suffix == "client-set suffix"

    @pytest.mark.asyncio
    async def test_stamp_does_not_introduce_a_current_datetime(self, tmp_path):
        """Synthesizing a context must not smuggle in AgentContext's defaults.

        ``current_datetime`` defaults to "now", and ACPAgent._render_suffix
        deliberately suppresses it when no context was supplied, so a bare
        ``AgentContext()`` here would start emitting a <CURRENT_DATETIME> block
        into prompts that previously had none.
        """
        persisted = PersistedSettings(
            agent_settings=OpenHandsAgentSettings(
                agent_context=AgentContext(load_memory=True)
            )
        )

        agent = await _start_with_agent(tmp_path, persisted, agent=_make_agent())

        assert agent.agent_context is not None
        assert agent.agent_context.load_memory is True
        assert agent.agent_context.current_datetime is None


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
        mock_conversation_service.start_conversation.side_effect = (
            UnresolvedProfileReferences(
                mcp_server_refs=["missing-server", "another-missing"]
            )
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
        assert detail["code"] == "unresolved_profile_references"
        assert "missing-server" in detail["dangling_mcp_server_refs"]
        assert detail["dangling_llm_profile_ref"] is None

    def test_dangling_llm_profile_ref_returns_422(
        self, client, mock_conversation_service
    ):
        """A launch never silently falls back to a different LLM, so the client
        gets a structured error it can turn into a fix-it prompt."""
        mock_conversation_service.start_conversation.side_effect = (
            UnresolvedProfileReferences(llm_profile_ref="gone")
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
        assert detail["dangling_llm_profile_ref"] == "gone"
        assert detail["dangling_mcp_server_refs"] == []

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

    def test_agent_sources_excluded_from_stored_conversation_persistence(self):
        """Regression: no agent source may appear in the StoredConversation payload.

        StartConversationRequest.model_dump() includes the launch inputs for HTTP
        transport. _start_conversation excludes them before building
        StoredConversation (they are resolved into the agent and
        launched_agent_profile).
        """
        profile_id = uuid4()
        request = StartConversationRequest(
            agent_profile_id=profile_id,
            workspace=LocalWorkspace(working_dir="/tmp"),
        )
        request_data = request.model_dump(
            mode="json",
            exclude={
                "agent_profile_id",
                "agent_profile",
                "agent_settings",
                "agent_launch_additions",
            },
        )
        request_data["agent"] = _make_agent().model_dump(mode="json")
        stored = StoredConversation(id=uuid4(), **request_data)
        dumped = stored.model_dump(mode="json")
        assert "agent_profile_id" not in dumped
        assert "agent_profile" not in dumped
        assert "agent_settings" not in dumped

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

    def _allowed_secrets(self, profile) -> frozenset[str] | None:
        _, plan = _launch(profile)
        return plan.allowed_secrets

    def test_an_unscoped_profile_reports_no_restriction(self):
        assert self._allowed_secrets(_make_openhands_profile()) is None

    def test_a_scoped_profile_reports_its_allow_list(self):
        profile = _make_openhands_profile().model_copy(
            update={"secret_refs": ["GITHUB_TOKEN"]}
        )
        assert self._allowed_secrets(profile) == {"GITHUB_TOKEN"}

    def test_a_scoped_acp_profile_gets_no_implicit_provider_credentials(self):
        # Strict: an ACP profile must list its own credential to receive it.
        profile = _make_acp_profile().model_copy(update={"secret_refs": []})
        assert self._allowed_secrets(profile) == set()

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
    def test_launch_drops_secrets_the_profile_disallows(self, secret_refs, expected):
        """The filter runs on the request, so a client cannot widen the scope."""
        profile = _make_openhands_profile().model_copy(
            update={"secret_refs": secret_refs}
        )

        request, _ = _launch(
            profile,
            secrets={
                "GITHUB_TOKEN": StaticSecret(value=SecretStr("gh")),
                "DATADOG_API_KEY": StaticSecret(value=SecretStr("dd")),
            },
        )

        assert set(request.secrets) == expected

    @pytest.mark.asyncio
    async def test_start_conversation_persists_only_the_allowed_secrets(self, tmp_path):
        profile = _make_openhands_profile().model_copy(
            update={"secret_refs": ["GITHUB_TOKEN"]}
        )
        _store_llm_profile()
        get_agent_profile_store().save(profile)
        request = StartConversationRequest(
            agent_profile_id=profile.id,
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
            secrets={
                "GITHUB_TOKEN": StaticSecret(value=SecretStr("gh")),
                "DATADOG_API_KEY": StaticSecret(value=SecretStr("dd")),
            },
        )
        captured: dict[str, Any] = {}

        async def capture(stored, **kwargs):
            captured["secrets"] = dict(stored.secrets)
            return _mock_event_service(
                ConversationState(
                    id=uuid4(),
                    agent=kwargs["agent"],
                    workspace=request.workspace,
                    execution_status=ConversationExecutionStatus.IDLE,
                )
            )

        async with ConversationService(
            conversations_dir=tmp_path / "conversations"
        ) as service:
            with (
                patch(_DISCOVER_PATH, return_value=[]),
                patch(_BROWSER_PROBE_PATH, return_value=False),
                patch.object(
                    service,
                    "_start_event_service",
                    new_callable=AsyncMock,
                    side_effect=capture,
                ),
            ):
                await service.start_conversation(request)

        assert set(captured["secrets"]) == {"GITHUB_TOKEN"}
