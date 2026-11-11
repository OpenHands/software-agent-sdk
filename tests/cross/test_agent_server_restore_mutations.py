"""Agent-server restore semantics for state owned by ``base_state.json``.

``confirmation_policy``, ``security_analyzer`` and ``secrets`` can change after a
conversation starts. Their only persisted home is the conversation's
``base_state.json``; ``meta.json`` does not carry them. These tests drive a real
``ConversationService`` through a restart (close, then a fresh service on the
same directory) and check that the changes survive, also for a fork made after
the restart (#4810).
"""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_service import EventService
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.request import (
    StartChildConversationRequest,
    StartConversationRequest,
)
from openhands.sdk.secret import StaticSecret
from openhands.sdk.security.confirmation_policy import AlwaysConfirm, NeverConfirm
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.workspace import LocalWorkspace


pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="The agent-server conversation service targets Unix hosts.",
)


def _request(workspace_dir) -> StartConversationRequest:
    return StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
        secrets={"START_TOKEN": StaticSecret(value=SecretStr("start-value"))},
    )


@pytest.mark.asyncio
async def test_mutations_survive_a_server_restart_and_a_later_fork(tmp_path):
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    cipher = Cipher("restore-mutations-key")

    async with ConversationService(
        conversations_dir=conversations_dir, cipher=cipher
    ) as service:
        info, _ = await service.start_conversation(_request(workspace_dir))
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        await event_service.set_confirmation_policy(AlwaysConfirm())
        await event_service.set_security_analyzer(LLMSecurityAnalyzer())
        await event_service.update_secrets({"ADDED_TOKEN": "added-value"})

    meta = json.loads((conversations_dir / info.id.hex / "meta.json").read_text())
    for field in ("confirmation_policy", "security_analyzer", "secrets"):
        assert field not in meta

    async with ConversationService(
        conversations_dir=conversations_dir, cipher=cipher
    ) as service:
        restored = await service.get_event_service(info.id)
        assert restored is not None
        state = await restored.get_state()
        assert isinstance(state.confirmation_policy, AlwaysConfirm)
        assert isinstance(state.security_analyzer, LLMSecurityAnalyzer)
        secrets = state.secret_registry.secret_sources
        assert secrets["START_TOKEN"].get_value() == "start-value"
        assert secrets["ADDED_TOKEN"].get_value() == "added-value"

        fork_info = await service.fork_conversation(info.id)
        assert fork_info is not None
        fork_service = await service.get_event_service(fork_info.id)
        assert fork_service is not None
        fork_state = await fork_service.get_state()
        assert isinstance(fork_state.confirmation_policy, AlwaysConfirm)
        assert isinstance(fork_state.security_analyzer, LLMSecurityAnalyzer)
        assert fork_state.security_analyzer is not state.security_analyzer
        fork_secrets = fork_state.secret_registry.secret_sources
        assert fork_secrets["START_TOKEN"].get_value() == "start-value"
        assert fork_secrets["ADDED_TOKEN"].get_value() == "added-value"


@pytest.mark.asyncio
async def test_secrets_an_older_server_left_in_meta_json_are_kept(tmp_path):
    """Older servers kept start-time secrets in meta.json and could leave them
    out of base_state.json. The first resume takes over the ones base_state.json
    lacks; a value it already has wins over the older copy."""
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    cipher = Cipher("restore-mutations-key")

    async with ConversationService(
        conversations_dir=conversations_dir, cipher=cipher
    ) as service:
        info, _ = await service.start_conversation(_request(workspace_dir))
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        await event_service.update_secrets({"START_TOKEN": "changed-value"})

    meta_file = conversations_dir / info.id.hex / "meta.json"
    meta = json.loads(meta_file.read_text())
    meta["secrets"] = {
        name: StaticSecret(value=SecretStr(value)).model_dump(
            mode="json", context={"cipher": cipher}
        )
        for name, value in {
            "START_TOKEN": "start-value",
            "ONLY_IN_META": "legacy-value",
        }.items()
    }
    meta_file.write_text(json.dumps(meta))

    async with ConversationService(
        conversations_dir=conversations_dir, cipher=cipher
    ) as service:
        restored = await service.get_event_service(info.id)
        assert restored is not None
        secrets = (await restored.get_state()).secret_registry.secret_sources
        assert secrets["ONLY_IN_META"].get_value() == "legacy-value"
        assert secrets["START_TOKEN"].get_value() == "changed-value"

    assert "secrets" not in json.loads(meta_file.read_text())
    base_state = json.loads((meta_file.parent / "base_state.json").read_text())
    assert "ONLY_IN_META" in base_state["secret_registry"]["secret_sources"]


@pytest.mark.asyncio
async def test_fork_keeps_secret_values_on_a_server_without_a_cipher(tmp_path):
    """Without a cipher the fork's base_state.json has no secret values, so the
    server hands the fork the ones its source holds in memory."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        info, _ = await service.start_conversation(_request(workspace_dir))
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        await event_service.update_secrets({"ADDED_TOKEN": "added-value"})

        fork_info = await service.fork_conversation(info.id)
        assert fork_info is not None
        fork_service = await service.get_event_service(fork_info.id)
        assert fork_service is not None
        secrets = (await fork_service.get_state()).secret_registry.secret_sources
        assert secrets["START_TOKEN"].get_value() == "start-value"
        assert secrets["ADDED_TOKEN"].get_value() == "added-value"


@pytest.mark.asyncio
async def test_conversations_started_from_one_request_have_their_own_analyzer(
    tmp_path,
):
    """An analyzer can keep per-conversation history, so two conversations
    never share one, also when the same request object starts both."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    request = _request(workspace_dir).model_copy(
        update={"security_analyzer": LLMSecurityAnalyzer()}
    )

    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        analyzers = []
        for _ in range(2):
            info, _ = await service.start_conversation(request)
            event_service = await service.get_event_service(info.id)
            assert event_service is not None
            analyzers.append((await event_service.get_state()).security_analyzer)

    assert all(isinstance(analyzer, LLMSecurityAnalyzer) for analyzer in analyzers)
    assert analyzers[0] is not analyzers[1]
    assert all(analyzer is not request.security_analyzer for analyzer in analyzers)


@pytest.mark.asyncio
async def test_retry_of_a_create_that_failed_halfway_gets_the_requested_settings(
    tmp_path,
):
    """A create can fail after base_state.json is written. The retry with the
    same id is still a create: it applies the request's policy and analyzer."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    request = _request(workspace_dir).model_copy(
        update={
            "conversation_id": uuid4(),
            "confirmation_policy": AlwaysConfirm(),
            "security_analyzer": LLMSecurityAnalyzer(),
        }
    )
    update_secrets = LocalConversation.update_secrets
    calls = 0

    def fail_the_first_save(self, secrets):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(28, "No space left on device")
        return update_secrets(self, secrets)

    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        with patch.object(LocalConversation, "update_secrets", fail_the_first_save):
            with pytest.raises(OSError):
                await service.start_conversation(request)
            info, _ = await service.start_conversation(request)

        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        state = await event_service.get_state()
        assert isinstance(state.confirmation_policy, AlwaysConfirm)
        assert isinstance(state.security_analyzer, LLMSecurityAnalyzer)


@pytest.mark.asyncio
async def test_child_inherits_what_its_parent_has_now(tmp_path):
    """A child started on a real service gets the parent's current policy,
    analyzer and secrets, changes made after the parent's start included, in
    its own base_state.json and not in its meta.json."""
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    async with ConversationService(
        conversations_dir=conversations_dir,
        conversation_worktree_root=tmp_path / "worktrees",
        cipher=Cipher("restore-mutations-key"),
    ) as service:
        parent, _ = await service.start_conversation(_request(workspace_dir))
        parent_service = await service.get_event_service(parent.id)
        assert parent_service is not None
        await parent_service.set_confirmation_policy(AlwaysConfirm())
        await parent_service.set_security_analyzer(LLMSecurityAnalyzer())
        await parent_service.update_secrets({"ADDED_TOKEN": "added-value"})

        with patch.object(EventService, "run", new=AsyncMock()):
            child = await service.start_child_conversation(
                parent.id, StartChildConversationRequest(task="Reply with ok.")
            )
        assert child is not None
        child_service = await service.get_event_service(child.conversation_id)
        assert child_service is not None
        parent_state = await parent_service.get_state()
        child_state = await child_service.get_state()

        assert isinstance(child_state.confirmation_policy, AlwaysConfirm)
        assert isinstance(child_state.security_analyzer, LLMSecurityAnalyzer)
        assert child_state.security_analyzer is not parent_state.security_analyzer
        secrets = child_state.secret_registry.secret_sources
        assert secrets["START_TOKEN"].get_value() == "start-value"
        assert secrets["ADDED_TOKEN"].get_value() == "added-value"

    child_dir = conversations_dir / child.conversation_id.hex
    base_state = json.loads((child_dir / "base_state.json").read_text())
    assert base_state["confirmation_policy"]["kind"] == "AlwaysConfirm"
    assert base_state["security_analyzer"]["kind"] == "LLMSecurityAnalyzer"
    assert set(base_state["secret_registry"]["secret_sources"]) == {
        "START_TOKEN",
        "ADDED_TOKEN",
    }
    meta = json.loads((child_dir / "meta.json").read_text())
    assert not {"confirmation_policy", "security_analyzer", "secrets"} & set(meta)
