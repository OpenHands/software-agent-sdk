"""Runtime mutations survive an eviction/restart (#4810).

``confirmation_policy``, ``security_analyzer`` and ``secrets`` changed through
the API live in ``ConversationState`` (``base_state.json``). Reloading the
conversation must keep them rather than re-applying the create-time values
held in ``meta.json``.
"""

from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StoredConversation
from openhands.sdk import LLM, Agent
from openhands.sdk.secret import StaticSecret
from openhands.sdk.security.confirmation_policy import AlwaysConfirm, NeverConfirm
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.workspace import LocalWorkspace


def _reload_stored(service: EventService) -> StoredConversation:
    meta = service.conversation_dir / "meta.json"
    return StoredConversation.model_validate_json(
        meta.read_text(), context={"cipher": service.cipher}
    )


@pytest.mark.asyncio
async def test_api_mutations_survive_reload(tmp_path):
    cipher = Cipher("test-secret-key")
    stored = StoredConversation(
        id=uuid4(),
        workspace=LocalWorkspace(working_dir=str(tmp_path / "workspace")),
        confirmation_policy=NeverConfirm(),
        secrets={"START_TOKEN": StaticSecret(value=SecretStr("start-value"))},
    )
    conversations_dir = tmp_path / "conversations"
    service = EventService(
        stored=stored,
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        conversations_dir=conversations_dir,
        cipher=cipher,
        lease_ttl_seconds=0,
    )
    await service.start()
    await service.save_meta()
    try:
        await service.set_confirmation_policy(AlwaysConfirm())
        await service.set_security_analyzer(LLMSecurityAnalyzer())
        await service.update_secrets(
            {"START_TOKEN": "overridden", "RUNTIME_TOKEN": "runtime-value"}
        )
    finally:
        await service.close()

    # Simulate eviction / restart: a fresh service from meta.json on disk.
    reloaded = EventService(
        stored=_reload_stored(service),
        conversations_dir=conversations_dir,
        cipher=cipher,
        lease_ttl_seconds=0,
    )
    await reloaded.start()
    try:
        state = await reloaded.get_state()
        assert isinstance(state.confirmation_policy, AlwaysConfirm)
        assert isinstance(state.security_analyzer, LLMSecurityAnalyzer)
        env = state.secret_registry.get_secrets_as_env_vars(
            "$START_TOKEN $RUNTIME_TOKEN"
        )
        assert env == {"START_TOKEN": "overridden", "RUNTIME_TOKEN": "runtime-value"}
    finally:
        await reloaded.close()
