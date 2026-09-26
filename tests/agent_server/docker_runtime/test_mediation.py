import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.mediation import (
    prepare_start,
    serialize_start,
    stage_title_profile,
)
from openhands.agent_server.docker_runtime.provisioning import RuntimeProvisioningStore
from openhands.agent_server.persistence import (
    get_agent_profile_store,
    get_llm_profile_store,
    get_provider_connections_store,
    reset_stores,
)
from openhands.sdk import LLM, Agent, Message, TextContent
from openhands.sdk.context import AgentContext
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.provider_connection_store import ProviderConnection
from openhands.sdk.profiles import OpenHandsAgentProfile
from openhands.sdk.secret import LookupSecret, StaticSecret
from openhands.sdk.workspace import LocalWorkspace


def config(tmp_path, monkeypatch) -> Config:
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    monkeypatch.setenv("OH_INTERNAL_SERVER_URL", "http://127.0.0.1:8123")
    return Config(
        conversations_path=tmp_path / "conversations",
        workspace_path=tmp_path / "workspaces",
        secret_key=SecretStr("outer-key"),
        session_api_keys=["outer-session"],
    )


@pytest.mark.asyncio
async def test_materializes_request_secret_sources(tmp_path, monkeypatch):
    runtime_config = config(tmp_path, monkeypatch)
    looked_up = []

    def get_value(secret):
        looked_up.append(secret.url)
        return "selected-value"

    monkeypatch.setattr(LookupSecret, "get_value", get_value)
    request = StartConversationRequest(
        workspace=LocalWorkspace(working_dir="/workspace"),
        agent=Agent(llm=LLM(model="test", api_key=SecretStr("model-key"))),
        secrets={
            "SELECTED": LookupSecret(
                url="/api/settings/secrets/SELECTED",
                headers={"X-Session-API-Key": "outer-session"},
            )
        },
    )

    prepared, launched = await prepare_start(
        request.model_dump(mode="json"), runtime_config
    )
    assert launched is None
    assert looked_up == ["http://127.0.0.1:8123/api/settings/secrets/SELECTED"]
    assert isinstance(prepared.secrets["SELECTED"], StaticSecret)

    identity = RuntimeProvisioningStore(runtime_config).create(uuid4())
    payload = serialize_start(prepared, identity)
    assert "selected-value" not in str(payload)
    assert "outer-session" not in str(payload)
    received = StartConversationRequest.model_validate(
        payload, context={"cipher": identity.cipher}
    )
    assert received.secrets["SELECTED"].get_value() == "selected-value"


@pytest.mark.asyncio
async def test_materializes_agent_context_secret_sources(tmp_path, monkeypatch):
    runtime_config = config(tmp_path, monkeypatch)
    monkeypatch.setattr(LookupSecret, "get_value", lambda secret: "context-value")
    request = StartConversationRequest(
        workspace=LocalWorkspace(working_dir="/workspace"),
        agent=Agent(
            llm=LLM(model="test"),
            agent_context=AgentContext(
                secrets={"CONTEXT_SECRET": LookupSecret(url="/secret")}
            ),
        ),
    )
    prepared, _ = await prepare_start(request.model_dump(mode="json"), runtime_config)
    context = prepared.agent.agent_context
    assert context is not None
    assert context.secrets is not None
    source = context.secrets["CONTEXT_SECRET"]
    assert isinstance(source, StaticSecret)
    assert source.get_value() == "context-value"


@pytest.mark.asyncio
async def test_profile_uses_existing_resolver_and_secret_allowlist(
    tmp_path, monkeypatch
):
    runtime_config = config(tmp_path, monkeypatch)
    get_llm_profile_store().save(
        "docker-test-model",
        LLM(model="test", api_key=SecretStr("model-key")),
        include_secrets=True,
        cipher=runtime_config.cipher,
    )
    profile = OpenHandsAgentProfile(
        name="docker-test-profile",
        llm_profile_ref="docker-test-model",
        tools=[],
        mcp_server_refs=[],
        secret_refs=["ALLOWED"],
    )
    get_agent_profile_store().save(profile)
    monkeypatch.setattr(
        "openhands.agent_server.conversation_service.discover_profile_skills",
        lambda: [],
    )
    monkeypatch.setattr(
        LookupSecret, "get_value", lambda secret: secret.url.rsplit("/", 1)[-1]
    )
    request = StartConversationRequest(
        workspace=LocalWorkspace(working_dir="/workspace"),
        agent_profile_id=profile.id,
        secrets={
            name: LookupSecret(url=f"/api/settings/secrets/{name}")
            for name in ("ALLOWED", "UNRELATED")
        },
    )

    prepared, launched = await prepare_start(
        request.model_dump(mode="json"), runtime_config
    )
    assert set(prepared.secrets) == {"ALLOWED"}
    assert prepared.agent_profile_id is None
    assert launched is not None
    assert launched.agent_profile_id == profile.id


def _title_request(profile: str | None) -> StartConversationRequest:
    return StartConversationRequest(
        workspace=LocalWorkspace(working_dir="/workspace"),
        agent=Agent(llm=LLM(model="test"), tools=[]),
        title_llm_profile=profile,
    )


def _runtime_profile_store(persistence_dir):
    # What ``get_llm_profile_store()`` resolves to inside the container, where
    # ``OH_PERSISTENCE_DIR`` is this bind-mounted directory.
    return LLMProfileStore(base_dir=persistence_dir / "profiles")


def test_stages_only_the_selected_title_profile_for_the_runtime(tmp_path, monkeypatch):
    runtime_config = config(tmp_path, monkeypatch)
    host_store = get_llm_profile_store()
    host_store.save(
        "title-aux",
        LLM(
            model="openai/aux",
            base_url="http://host.docker.internal:1234/v1",
            api_key=SecretStr("aux-secret"),
        ),
        include_secrets=True,
        cipher=runtime_config.cipher,
    )
    host_store.save(
        "unrelated",
        LLM(model="openai/other", api_key=SecretStr("unrelated-secret")),
        include_secrets=True,
        cipher=runtime_config.cipher,
    )
    provisioning = RuntimeProvisioningStore(runtime_config)
    identity = provisioning.create(uuid4())
    persistence_dir = provisioning.persistence_dir(identity.conversation_id)

    stage_title_profile(
        _title_request("title-aux"), identity, provisioning, runtime_config
    )

    runtime_store = _runtime_profile_store(persistence_dir)
    assert runtime_store.list() == ["title-aux.json"]
    staged = runtime_store.load("title-aux", cipher=identity.cipher)
    assert staged.model == "openai/aux"
    assert staged.base_url == "http://host.docker.internal:1234/v1"
    assert isinstance(staged.api_key, SecretStr)
    assert staged.api_key.get_secret_value() == "aux-secret"
    on_disk = (persistence_dir / "profiles" / "title-aux.json").read_text()
    assert "aux-secret" not in on_disk
    assert "unrelated-secret" not in on_disk
    # The host key is not the runtime key: it must not recover the secret.
    with_host_key = runtime_store.load("title-aux", cipher=runtime_config.cipher)
    assert with_host_key.api_key is None
    assert persistence_dir.stat().st_mode & 0o777 == 0o700
    assert not list((persistence_dir / "provider-connections").glob("*.json"))
    assert sorted(host_store.list()) == ["title-aux.json", "unrelated.json"]


def test_staged_profile_keeps_credentials_resolved_from_its_provider_connection(
    tmp_path, monkeypatch
):
    runtime_config = config(tmp_path, monkeypatch)
    get_provider_connections_store().create(
        ProviderConnection(
            id="lmstudio",
            display_name="LM Studio",
            api_key=SecretStr("connection-secret"),
            base_url="http://host.docker.internal:1234/v1",
            created_at=0,
            updated_at=0,
        ),
        cipher=runtime_config.cipher,
    )
    get_llm_profile_store().save(
        "linked",
        LLM(model="openai/aux", provider_connection_id="lmstudio"),
        include_secrets=True,
        cipher=runtime_config.cipher,
    )
    provisioning = RuntimeProvisioningStore(runtime_config)
    identity = provisioning.create(uuid4())
    persistence_dir = provisioning.persistence_dir(identity.conversation_id)

    stage_title_profile(
        _title_request("linked"), identity, provisioning, runtime_config
    )

    # The runtime store has no provider connections, exactly like the container.
    staged = _runtime_profile_store(persistence_dir).load(
        "linked", cipher=identity.cipher
    )
    assert staged.provider_connection_id is None
    assert isinstance(staged.api_key, SecretStr)
    assert staged.api_key.get_secret_value() == "connection-secret"
    assert staged.base_url == "http://host.docker.internal:1234/v1"
    on_disk = (persistence_dir / "profiles" / "linked.json").read_text()
    assert "connection-secret" not in on_disk
    assert not list((persistence_dir / "provider-connections").glob("*.json"))


@pytest.mark.parametrize(
    "profile",
    ["absent", "../escape", "corrupt"],
    ids=["missing", "invalid-name", "unloadable"],
)
def test_unusable_title_profile_stages_nothing_and_does_not_fail(
    tmp_path, monkeypatch, profile
):
    runtime_config = config(tmp_path, monkeypatch)
    host_store = get_llm_profile_store()
    (host_store.base_dir / "corrupt.json").write_text("not json")
    provisioning = RuntimeProvisioningStore(runtime_config)
    identity = provisioning.create(uuid4())
    persistence_dir = provisioning.persistence_dir(identity.conversation_id)

    stage_title_profile(_title_request(profile), identity, provisioning, runtime_config)

    assert not persistence_dir.exists()
    assert not list(provisioning.data_root.rglob("*.json"))


def test_no_title_profile_leaves_the_runtime_untouched(tmp_path, monkeypatch):
    runtime_config = config(tmp_path, monkeypatch)
    provisioning = RuntimeProvisioningStore(runtime_config)
    identity = provisioning.create(uuid4())
    persistence_dir = provisioning.persistence_dir(identity.conversation_id)

    stage_title_profile(_title_request(None), identity, provisioning, runtime_config)

    assert not persistence_dir.exists()


def test_staged_profiles_stay_isolated_between_runtimes(tmp_path, monkeypatch):
    runtime_config = config(tmp_path, monkeypatch)
    get_llm_profile_store().save(
        "title-aux",
        LLM(model="openai/aux", api_key=SecretStr("aux-secret")),
        include_secrets=True,
        cipher=runtime_config.cipher,
    )
    provisioning = RuntimeProvisioningStore(runtime_config)
    first = provisioning.create(uuid4())
    second = provisioning.create(uuid4())
    for identity in (first, second):
        stage_title_profile(
            _title_request("title-aux"),
            identity,
            provisioning,
            runtime_config,
        )

    first_store = _runtime_profile_store(
        provisioning.persistence_dir(first.conversation_id)
    )
    own = first_store.load("title-aux", cipher=first.cipher)
    assert isinstance(own.api_key, SecretStr)
    assert own.api_key.get_secret_value() == "aux-secret"
    assert first_store.load("title-aux", cipher=second.cipher).api_key is None


@pytest.mark.asyncio
async def test_runtime_auto_title_uses_the_staged_profile_for_an_acp_style_agent(
    tmp_path, monkeypatch, request
):
    """Outer staging -> inner ``AutoTitleSubscriber._load_title_llm`` with the
    runtime cipher, for an agent whose LLM is the inert ACP sentinel.

    Only the LLM transport (``LLM.generate``) is replaced; the profile store,
    the subscriber and the cipher path are real.
    """
    from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse

    from openhands.agent_server.conversation_service import AutoTitleSubscriber
    from openhands.agent_server.event_service import EventService
    from openhands.agent_server.models import StoredConversation
    from openhands.sdk.event import MessageEvent
    from openhands.sdk.llm import LLMResponse, MetricsSnapshot
    from openhands.sdk.security.confirmation_policy import NeverConfirm

    runtime_config = config(tmp_path, monkeypatch)
    get_llm_profile_store().save(
        "title-aux",
        LLM(model="openai/aux", api_key=SecretStr("aux-secret"), usage_id="title-aux"),
        include_secrets=True,
        cipher=runtime_config.cipher,
    )
    provisioning = RuntimeProvisioningStore(runtime_config)
    identity = provisioning.create(uuid4())
    persistence_dir = provisioning.persistence_dir(identity.conversation_id)
    stage_title_profile(
        _title_request("title-aux"), identity, provisioning, runtime_config
    )

    # Inner runtime: OH_PERSISTENCE_DIR is the mounted runtime directory and the
    # service cipher is the runtime key from OH_SECRET_KEY.
    reset_stores()
    request.addfinalizer(reset_stores)
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(persistence_dir))
    service = AsyncMock(spec=EventService)
    service.stored = StoredConversation(
        id=identity.conversation_id,
        workspace=LocalWorkspace(working_dir="/workspace"),
        confirmation_policy=NeverConfirm(),
        title_llm_profile="title-aux",
    )
    service.cipher = identity.cipher
    service._conversation = SimpleNamespace(
        agent=SimpleNamespace(llm=LLM(model="acp-managed", usage_id="acp-managed"))
    )
    calls: list[tuple[str, str | None]] = []

    def fake_generate(self_llm, _messages, **_kwargs):
        calls.append(
            (
                self_llm.usage_id,
                self_llm.api_key.get_secret_value() if self_llm.api_key else None,
            )
        )
        choice = Choices(
            finish_reason="stop",
            index=0,
            message=LiteLLMMessage(content="Staged title", role="assistant"),
        )
        return LLMResponse(
            message=Message.from_llm_chat_message(choice["message"]),
            metrics=MetricsSnapshot(
                model_name=self_llm.model,
                accumulated_cost=0.0,
                max_budget_per_task=None,
                accumulated_token_usage=None,
            ),
            raw_response=ModelResponse(
                id="resp-1",
                choices=[choice],
                created=0,
                model=self_llm.model,
                object="chat.completion",
            ),
        )

    with patch(
        "openhands.sdk.llm.llm.LLM.generate", autospec=True, side_effect=fake_generate
    ):
        await AutoTitleSubscriber(service=service)(
            MessageEvent(
                id="evt-1",
                source="user",
                llm_message=Message(
                    role="user", content=[TextContent(text="Fix the login bug")]
                ),
            )
        )
        for _ in range(100):
            await asyncio.sleep(0.02)
            if service.stored.title is not None:
                break

    assert calls == [("title-aux", "aux-secret")]
    assert service.stored.title == "Staged title"


def test_established_conversation_keeps_its_staged_snapshot(tmp_path, monkeypatch):
    runtime_config = config(tmp_path, monkeypatch)
    host_store = get_llm_profile_store()
    host_store.save(
        "title-aux",
        LLM(model="openai/aux", api_key=SecretStr("aux-secret")),
        include_secrets=True,
        cipher=runtime_config.cipher,
    )
    provisioning = RuntimeProvisioningStore(runtime_config)
    identity = provisioning.create(uuid4())
    stage_title_profile(
        _title_request("title-aux"), identity, provisioning, runtime_config
    )
    persistence_dir = provisioning.persistence_dir(identity.conversation_id)
    original = (persistence_dir / "profiles" / "title-aux.json").read_bytes()
    # The inner server establishes the conversation in the mounted directory.
    conversation_dir = runtime_config.conversations_path / identity.conversation_id.hex
    conversation_dir.mkdir(parents=True)
    (conversation_dir / "meta.json").write_text("{}")
    host_store.save(
        "title-aux",
        LLM(model="openai/host-edit", api_key=SecretStr("rotated-secret")),
        include_secrets=True,
        cipher=runtime_config.cipher,
    )
    host_store.save(
        "other",
        LLM(model="openai/other", api_key=SecretStr("other-secret")),
        include_secrets=True,
        cipher=runtime_config.cipher,
    )

    for profile in ("title-aux", "other", None):
        stage_title_profile(
            _title_request(profile), identity, provisioning, runtime_config
        )

    assert _runtime_profile_store(persistence_dir).list() == ["title-aux.json"]
    assert (persistence_dir / "profiles" / "title-aux.json").read_bytes() == original


def test_incomplete_attempt_is_replaced_by_the_retried_selection(tmp_path, monkeypatch):
    runtime_config = config(tmp_path, monkeypatch)
    host_store = get_llm_profile_store()
    for name in ("title-aux", "other"):
        host_store.save(
            name,
            LLM(model=f"openai/{name}", api_key=SecretStr(f"{name}-secret")),
            include_secrets=True,
            cipher=runtime_config.cipher,
        )
    provisioning = RuntimeProvisioningStore(runtime_config)
    identity = provisioning.create(uuid4())
    persistence_dir = provisioning.persistence_dir(identity.conversation_id)
    stage_title_profile(
        _title_request("title-aux"), identity, provisioning, runtime_config
    )

    stage_title_profile(_title_request("other"), identity, provisioning, runtime_config)
    assert _runtime_profile_store(persistence_dir).list() == ["other.json"]

    stage_title_profile(_title_request(None), identity, provisioning, runtime_config)
    assert _runtime_profile_store(persistence_dir).list() == []


def test_concurrent_staging_attempts_leave_exactly_one_profile(tmp_path, monkeypatch):
    """The establish-check and the replace of a stale attempt run under the
    runtime's own lock, so interleaved attempts can never leave two profiles."""
    runtime_config = config(tmp_path, monkeypatch)
    host_store = get_llm_profile_store()
    names = ["title-aux", "other"]
    for name in names:
        host_store.save(
            name,
            LLM(model=f"openai/{name}", api_key=SecretStr(f"{name}-secret")),
            include_secrets=True,
            cipher=runtime_config.cipher,
        )
    provisioning = RuntimeProvisioningStore(runtime_config)
    identity = provisioning.create(uuid4())
    persistence_dir = provisioning.persistence_dir(identity.conversation_id)
    errors: list[BaseException] = []

    def attempt(name: str) -> None:
        try:
            for _ in range(10):
                stage_title_profile(
                    _title_request(name), identity, provisioning, runtime_config
                )
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=attempt, args=(name,)) for name in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert errors == []
    staged = _runtime_profile_store(persistence_dir).list()
    assert len(staged) == 1
    assert staged[0] in {"title-aux.json", "other.json"}
