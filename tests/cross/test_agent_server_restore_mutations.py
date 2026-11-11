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

import pytest
from pydantic import SecretStr

from openhands.agent_server.conversation_service import ConversationService
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.request import StartConversationRequest
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
