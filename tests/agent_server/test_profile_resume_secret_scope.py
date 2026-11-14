"""Profile scope remains authoritative across Codex credential attachment/resume."""

import json

import pytest
from pydantic import SecretStr

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.credential_binding import LocalVersionedCredentialBinding
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.persistence import ServerStores
from openhands.sdk.credential import CredentialAuthorizationRejected
from openhands.sdk.profiles.agent_profile import ACPAgentProfile
from openhands.sdk.secret import StaticSecret
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def scoped_credentials(tmp_path):
    """A server's stores under ``tmp_path / "settings"``, and their cipher."""
    cipher = Cipher("synthetic-profile-scope-key")
    settings_dir = tmp_path / "settings"
    stores = ServerStores(
        cipher=cipher, profile_dir=settings_dir, workspace_dir=settings_dir
    )
    return stores, cipher


@pytest.mark.asyncio
@pytest.mark.parametrize("secret_refs", [None, [], ["CODEX_AUTH_JSON"]])
async def test_profile_scope_controls_saved_codex_binding_after_restart(
    tmp_path, scoped_credentials, secret_refs
):
    stores, cipher = scoped_credentials
    store = stores.secrets
    store.set_secret(
        "CODEX_AUTH_JSON",
        json.dumps({"auth_mode": "chatgpt", "tokens": {"refresh_token": "dummy"}}),
    )
    profile = ACPAgentProfile(
        name="codex-scope", acp_server="codex", secret_refs=secret_refs
    )
    stores.agent_profiles.save(profile)
    request = StartConversationRequest(
        agent_profile_id=profile.id,
        workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
    )
    expected = {"CODEX_AUTH_JSON"} if secret_refs != [] else set()
    async with ConversationService(
        conversations_dir=tmp_path / "conversations", stores=stores, cipher=cipher
    ) as service:
        info, _ = await service.start_conversation(request)
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        assert set(event_service.credential_bindings) == expected
        if not expected:
            with pytest.raises(CredentialAuthorizationRejected):
                await service.activate_credential_binding(
                    info.id,
                    "CODEX_AUTH_JSON",
                    LocalVersionedCredentialBinding(store, "CODEX_AUTH_JSON"),
                )
    stores.agent_profiles.save(profile.model_copy(update={"secret_refs": None}))
    async with ConversationService(
        conversations_dir=tmp_path / "conversations", stores=stores, cipher=cipher
    ) as service:
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        assert set(event_service.credential_bindings) == expected
        launched = event_service.stored.launched_agent_profile
        assert launched is not None and launched.secret_refs == secret_refs


@pytest.mark.asyncio
@pytest.mark.parametrize("secret_refs", [None, [], ["CODEX_AUTH_JSON"]])
@pytest.mark.parametrize("cold_resume", [False, True])
async def test_profile_scope_filters_resume_supplied_codex_secret(
    tmp_path, scoped_credentials, secret_refs, cold_resume
):
    stores, cipher = scoped_credentials
    profile = ACPAgentProfile(
        name="codex-scope", acp_server="codex", secret_refs=secret_refs
    )
    stores.agent_profiles.save(profile)
    request = StartConversationRequest(
        agent_profile_id=profile.id,
        workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
    )
    async with ConversationService(
        conversations_dir=tmp_path / "conversations", stores=stores, cipher=cipher
    ) as service:
        info, _ = await service.start_conversation(request)
        if cold_resume:
            await service.prepare_for_sandbox_pause()
        stores.agent_profiles.save(profile.model_copy(update={"secret_refs": None}))
        resume = request.model_copy(
            update={
                "conversation_id": info.id,
                "secrets": {"CODEX_AUTH_JSON": StaticSecret(value=SecretStr("dummy"))},
            }
        )
        await service.start_conversation(resume)
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        state = await event_service.get_state()
        expected = {"CODEX_AUTH_JSON"} if secret_refs != [] else set()
        assert set(state.secret_registry.secret_sources) == expected
        await event_service.update_secrets(
            {"CODEX_AUTH_JSON": StaticSecret(value=SecretStr("updated-dummy"))}
        )
        state = await event_service.get_state()
        assert set(state.secret_registry.secret_sources) == expected
