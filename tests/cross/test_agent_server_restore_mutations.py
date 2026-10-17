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
