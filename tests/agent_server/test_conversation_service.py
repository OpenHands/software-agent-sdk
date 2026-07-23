import asyncio
import json
import socket
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from litellm.types.utils import ChatCompletionMessageToolCall, Function
from pydantic import SecretStr

from openhands.agent_server.conversation_lease import (
    LEASE_FILE_NAME,
    ConversationOwnershipLostError,
)
from openhands.agent_server.conversation_service import (
    AutoTitleSubscriber,
    ConversationService,
    _get_worktree_start_point,
)
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import (
    ACPConversationInfo,
    ConversationInfo,
    ConversationPage,
    ConversationSortOrder,
    StartConversationRequest,
    StoredConversation,
    UpdateConversationRequest,
)
from openhands.agent_server.utils import safe_rmtree as _safe_rmtree
from openhands.sdk import LLM, Agent, Message
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.credential import CredentialSyncError
from openhands.sdk.critic.impl.api import APIBasedCritic
from openhands.sdk.event import ActionEvent, AgentErrorEvent, ObservationEvent
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.git.utils import run_git_command
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.mcp.config import dump_mcp_config
from openhands.sdk.secret import SecretSource, StaticSecret
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.terminal.definition import TerminalAction, TerminalObservation


@pytest.fixture
def mock_event_service():
    """Create a mock EventService with stored conversation data."""
    service = AsyncMock(spec=EventService)
    return service


@pytest.fixture
def sample_stored_conversation():
    """Create a sample StoredConversation for testing."""
    return StoredConversation(
        id=uuid4(),
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir="workspace/project"),
        confirmation_policy=NeverConfirm(),
        initial_message=None,
        metrics=None,
        created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
    )


def _create_running_terminal_action(tool_call_id: str = "call_1") -> ActionEvent:
    tool_call = MessageToolCall.from_chat_tool_call(
        ChatCompletionMessageToolCall(
            id=tool_call_id,
            type="function",
            function=Function(
                name="terminal",
                arguments='{"command": "sleep 30"}',
            ),
        )
    )
    return ActionEvent(
        thought=[TextContent(text="run sleep")],
        action=TerminalAction(command="sleep 30"),
        tool_name="terminal",
        tool_call_id=tool_call_id,
        tool_call=tool_call,
        llm_response_id="response_1",
        security_risk=SecurityRisk.LOW,
        summary="run sleep",
    )


def _expire_conversation_lease(conversations_dir: Path, conversation_id) -> None:
    lease_path = conversations_dir / conversation_id.hex / LEASE_FILE_NAME
    payload = json.loads(lease_path.read_text())
    payload["expires_at"] = 0
    lease_path.write_text(json.dumps(payload))


def _init_git_repo(repo_dir: Path) -> None:
    repo_dir.mkdir()
    (repo_dir / "README.md").write_text("# test repo\n")
    run_git_command(["git", "init", "-b", "main"], repo_dir)
    run_git_command(["git", "add", "README.md"], repo_dir)
    run_git_command(
        [
            "git",
            "-c",
            "user.name=OpenHands Test",
            "-c",
            "user.email=openhands@example.com",
            "commit",
            "-m",
            "init",
        ],
        repo_dir,
    )


@pytest.fixture
def conversation_service():
    """Create a ConversationService instance for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        service = ConversationService(
            conversations_dir=Path(temp_dir) / "conversations",
        )
        # Initialize the _event_services dict to simulate an active service
        service._event_services = {}
        yield service


@pytest.fixture
async def persisted_conversation(tmp_path) -> tuple[Path, UUID]:
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )
    async with ConversationService(conversations_dir=conversations_dir) as service:
        conversation_info, _ = await service.start_conversation(request)
    return conversations_dir, conversation_info.id


@pytest.mark.asyncio
async def test_start_conversation_registers_and_injects_client_tools(
    conversation_service, tmp_path
):
    """client_tools specs are registered, injected into the agent, and persisted.

    Persistence on ``StoredConversation`` is what allows forks and server
    restarts to re-register the dynamic client tools.
    """
    from openhands.sdk.tool.client_tool import ClientToolSpec

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
        client_tools=[
            ClientToolSpec(
                name="srv_show_dialog",
                description="Show a dialog",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            )
        ],
    )

    captured: dict[str, StoredConversation] = {}

    async def fake_start_event_service(stored: StoredConversation, **_kwargs):
        captured["stored"] = stored
        service = AsyncMock(spec=EventService)
        service.stored = stored
        service.get_state.return_value = ConversationState(
            id=stored.id,
            agent=stored.agent,
            workspace=stored.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=stored.confirmation_policy,
        )
        return service

    with patch.object(
        conversation_service,
        "_start_event_service",
        side_effect=fake_start_event_service,
    ):
        await conversation_service.start_conversation(request)

    stored = captured["stored"]
    # Injected into the agent's tool specs so _initialize() can resolve it
    assert "srv_show_dialog" in {t.name for t in stored.agent.tools}
    # Persisted so forks / restarts can re-register the dynamic action type
    assert [s.name for s in stored.client_tools] == ["srv_show_dialog"]
    # The class is registered in the global tool registry
    from openhands.sdk.tool.registry import list_registered_tools

    assert "srv_show_dialog" in list_registered_tools()


@pytest.mark.asyncio
async def test_start_conversation_decrypts_encrypted_agent_settings_mcp_env(
    conversation_service, tmp_path
):
    cipher = Cipher("mcp-env-test-key")
    conversation_service.cipher = cipher
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    encrypted_llm_key = cipher.encrypt(SecretStr("sk-plaintext"))
    encrypted_mcp_token = cipher.encrypt(SecretStr("ghp-plaintext"))
    request = StartConversationRequest(
        agent_settings={
            "schema_version": 1,
            "agent_kind": "llm",
            "llm": {
                "model": "gpt-4o",
                "usage_id": "test-llm",
                "api_key": encrypted_llm_key,
            },
            "tools": [],
            "mcp_config": {
                "mcpServers": {
                    "github": {
                        "command": "npx",
                        "env": {
                            "GITHUB_PERSONAL_ACCESS_TOKEN": encrypted_mcp_token,
                        },
                    }
                }
            },
        },
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
        secrets_encrypted=True,
    )
    assert (
        dump_mcp_config(request.agent.mcp_config)["github"]["env"][
            "GITHUB_PERSONAL_ACCESS_TOKEN"
        ]
        == encrypted_mcp_token
    )

    captured: dict[str, StoredConversation] = {}

    async def fake_start_event_service(stored: StoredConversation, **_kwargs):
        captured["stored"] = stored
        service = AsyncMock(spec=EventService)
        service.stored = stored
        service.get_state.return_value = ConversationState(
            id=stored.id,
            agent=stored.agent,
            workspace=stored.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=stored.confirmation_policy,
        )
        return service

    with patch.object(
        conversation_service,
        "_start_event_service",
        side_effect=fake_start_event_service,
    ):
        await conversation_service.start_conversation(request)

    stored = captured["stored"]
    assert isinstance(stored.agent.llm.api_key, SecretStr)
    assert stored.agent.llm.api_key.get_secret_value() == "sk-plaintext"
    assert (
        dump_mcp_config(stored.agent.mcp_config)["github"]["env"][
            "GITHUB_PERSONAL_ACCESS_TOKEN"
        ]
        == "ghp-plaintext"
    )


@pytest.mark.asyncio
async def test_second_service_does_not_resume_active_running_conversation(tmp_path):
    """A second service should not attach to a live running conversation."""
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )

    async with ConversationService(conversations_dir=conversations_dir) as primary:
        conversation_info, _ = await primary.start_conversation(request)
        assert primary._event_services is not None

        primary_event_service = primary._event_services[conversation_info.id]
        primary_state = await primary_event_service.get_state()

        running_action = _create_running_terminal_action()
        primary_state.events.append(running_action)
        primary_state.execution_status = ConversationExecutionStatus.RUNNING

        async with ConversationService(
            conversations_dir=conversations_dir,
        ) as secondary:
            assert secondary._event_services is not None
            assert conversation_info.id not in secondary._event_services

            primary_state.events.append(
                ObservationEvent(
                    observation=TerminalObservation.from_text(
                        "done",
                        command="sleep 30",
                        exit_code=0,
                    ),
                    action_id=running_action.id,
                    tool_name="terminal",
                    tool_call_id=running_action.tool_call_id,
                )
            )

        events = primary_state.events[:]
        assert [type(event).__name__ for event in events] == [
            "ActionEvent",
            "ConversationStateUpdateEvent",
            "ObservationEvent",
        ]
        assert not any(isinstance(event, AgentErrorEvent) for event in events)


@pytest.mark.asyncio
async def test_stale_owner_cannot_append_after_lease_takeover(tmp_path):
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )

    async with ConversationService(conversations_dir=conversations_dir) as primary:
        conversation_info, _ = await primary.start_conversation(request)
        assert primary._event_services is not None
        primary_event_service = primary._event_services[conversation_info.id]
        primary_state = await primary_event_service.get_state()

        running_action = _create_running_terminal_action()
        primary_state.events.append(running_action)
        primary_state.execution_status = ConversationExecutionStatus.RUNNING
        _expire_conversation_lease(conversations_dir, conversation_info.id)

        async with ConversationService(
            conversations_dir=conversations_dir,
        ) as secondary:
            assert secondary._event_services is not None
            secondary_event_service = secondary._event_services[conversation_info.id]
            secondary_state = await secondary_event_service.get_state()

            assert any(
                isinstance(event, AgentErrorEvent)
                for event in secondary_state.events[:]
            )

            with pytest.raises(ConversationOwnershipLostError):
                primary_state.events.append(
                    ObservationEvent(
                        observation=TerminalObservation.from_text(
                            "late result",
                            command="sleep 30",
                            exit_code=0,
                        ),
                        action_id=running_action.id,
                        tool_name="terminal",
                        tool_call_id=running_action.tool_call_id,
                    )
                )

            with pytest.raises(ConversationOwnershipLostError):
                primary_state.execution_status = ConversationExecutionStatus.ERROR


@pytest.mark.asyncio
async def test_event_services_use_centralized_lease_renewal(tmp_path):
    """Event services created by ConversationService should not spawn
    their own lease renewal tasks — renewal is handled centrally."""
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )

    async with ConversationService(conversations_dir=conversations_dir) as svc:
        info, _ = await svc.start_conversation(request)
        assert svc._event_services is not None
        es = svc._event_services[info.id]

        # Per-service renewal task should NOT be created
        assert es._lease_task is None
        assert es._external_lease_renewal is True

        # Centralized task should exist
        assert svc._lease_renewal_task is not None
        assert not svc._lease_renewal_task.done()

    # After __aexit__, centralized task should be cleaned up
    assert svc._lease_renewal_task is None


@pytest.mark.asyncio
async def test_centralized_lease_renewal_invokes_renew(tmp_path):
    """The centralized loop calls renew_lease() on every active service."""
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )

    with patch(
        "openhands.agent_server.conversation_service.LEASE_RENEW_INTERVAL_SECONDS",
        0.05,
    ):
        async with ConversationService(conversations_dir=conversations_dir) as svc:
            info1, _ = await svc.start_conversation(request)
            info2, _ = await svc.start_conversation(request)
            assert svc._event_services is not None
            es1 = svc._event_services[info1.id]
            es2 = svc._event_services[info2.id]

            renew_calls: dict[str, int] = {"es1": 0, "es2": 0}
            original_renew1 = es1.renew_lease
            original_renew2 = es2.renew_lease

            def counting_renew1():
                renew_calls["es1"] += 1
                original_renew1()

            def counting_renew2():
                renew_calls["es2"] += 1
                original_renew2()

            es1.renew_lease = counting_renew1  # type: ignore[method-assign]
            es2.renew_lease = counting_renew2  # type: ignore[method-assign]

            # Wait for at least 2 renewal cycles
            await asyncio.sleep(0.15)

            assert renew_calls["es1"] >= 1, "renew_lease not called on es1"
            assert renew_calls["es2"] >= 1, "renew_lease not called on es2"


@pytest.mark.asyncio
async def test_event_services_share_dedicated_run_executor(tmp_path):
    """Event services created by ConversationService should share a single
    dedicated thread pool for conversation.run() calls."""
    from concurrent.futures import ThreadPoolExecutor

    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )

    async with ConversationService(
        conversations_dir=conversations_dir, max_concurrent_runs=5
    ) as svc:
        info, _ = await svc.start_conversation(request)
        assert svc._event_services is not None
        es = svc._event_services[info.id]

        # A dedicated executor should exist on the service
        assert svc._run_executor is not None
        assert isinstance(svc._run_executor, ThreadPoolExecutor)
        assert svc._run_executor._max_workers == 5

        # EventService should share the same executor instance
        assert es._run_executor is svc._run_executor

    # After __aexit__, executor should be shut down
    assert svc._run_executor is None


@pytest.mark.asyncio
async def test_shutdown_retains_credential_close_failure_for_retry(tmp_path):
    service = ConversationService(conversations_dir=tmp_path / "conversations")
    await service.__aenter__()
    conversation_id = uuid4()
    runtime = AsyncMock(spec=EventService)
    runtime.__aexit__.side_effect = [
        CredentialSyncError("broker unavailable"),
        None,
    ]
    record = MagicMock()
    binding = MagicMock()
    assert service._event_services is not None
    service._event_services[conversation_id] = runtime
    service._conversation_records[conversation_id] = record
    service._credential_bindings[conversation_id] = {"CODEX_AUTH_JSON": binding}

    with pytest.raises(CredentialSyncError, match="broker unavailable"):
        await service.__aexit__(None, None, None)

    assert service._event_services == {conversation_id: runtime}
    assert service._conversation_records == {conversation_id: record}
    assert service._credential_bindings == {
        conversation_id: {"CODEX_AUTH_JSON": binding}
    }
    assert service._run_executor is None
    assert service._lease_renewal_task is not None
    assert not service._lease_renewal_task.done()

    await service.__aexit__(None, None, None)

    assert runtime.__aexit__.await_count == 2
    assert service._event_services is None
    assert service._conversation_records == {}
    assert service._credential_bindings == {}


@pytest.mark.asyncio
async def test_shutdown_prioritizes_credential_failure_across_runtimes(tmp_path):
    service = ConversationService(conversations_dir=tmp_path / "conversations")
    await service.__aenter__()
    first_id = uuid4()
    second_id = uuid4()
    first = AsyncMock(spec=EventService)
    second = AsyncMock(spec=EventService)
    first.__aexit__.side_effect = OSError("save failed")
    second.__aexit__.side_effect = CredentialSyncError("broker unavailable")
    assert service._event_services is not None
    service._event_services = {first_id: first, second_id: second}
    service._conversation_records = {
        first_id: MagicMock(),
        second_id: MagicMock(),
    }

    with pytest.raises(CredentialSyncError, match="broker unavailable"):
        await service.__aexit__(None, None, None)

    assert service._event_services == {first_id: first, second_id: second}

    first.__aexit__.side_effect = None
    second.__aexit__.side_effect = None
    await service.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_restart_resumes_conversations_after_non_graceful_shutdown(tmp_path):
    """Reproduces the crash-recovery bug: after a non-graceful shutdown the lease
    file is left on disk pointing at a still-future expires_at. A fresh server
    started before the TTL elapses must still pick up the conversation rather
    than skipping it for up to the full TTL window.
    """
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )

    async with ConversationService(conversations_dir=conversations_dir) as primary:
        conversation_info, _ = await primary.start_conversation(request)
        conversation_id = conversation_info.id

    # Simulate a non-graceful shutdown: forge a lease pointing at a PID
    # that is guaranteed not to be running, with a far-future expires_at.
    # A clean exit would have removed the lease via release(); a crash
    # leaves it behind, which is what we are reproducing here.
    lease_path = conversations_dir / conversation_id.hex / LEASE_FILE_NAME
    forged_payload = {
        "owner_instance_id": "ghost-instance-from-crashed-server",
        "generation": 1,
        "expires_at": time.time() + 3600.0,
        "owner_host": socket.gethostname(),
        "owner_pid": 2**31 - 1,
    }
    lease_path.write_text(json.dumps(forged_payload))

    async with ConversationService(conversations_dir=conversations_dir) as restarted:
        assert restarted._event_services is not None
        assert conversation_id not in restarted._event_services
        restarted_event_service = await restarted.get_event_service(conversation_id)
        assert restarted_event_service is not None, (
            "Lazy hydration failed to pick up an existing conversation whose "
            "lease was left orphaned by a non-graceful shutdown."
        )


@pytest.mark.asyncio
async def test_startup_and_search_do_not_hydrate_idle_conversation(tmp_path):
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )
    async with ConversationService(conversations_dir=conversations_dir) as primary:
        conversation_info, _ = await primary.start_conversation(request)

    restarted = ConversationService(conversations_dir=conversations_dir)
    with patch.object(
        restarted,
        "_start_event_service",
        side_effect=AssertionError("idle conversation should remain unloaded"),
    ):
        async with restarted:
            assert restarted._event_services == {}
            assert conversation_info.id in restarted._conversation_records

            page = await restarted.search_conversations()
            assert [item.id for item in page.items] == [conversation_info.id]
            assert await restarted.count_conversations() == 1
            assert restarted._event_services == {}


@pytest.mark.asyncio
async def test_concurrent_access_hydrates_conversation_once(tmp_path):
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )
    async with ConversationService(conversations_dir=conversations_dir) as primary:
        conversation_info, _ = await primary.start_conversation(request)

    async with ConversationService(conversations_dir=conversations_dir) as restarted:
        with patch.object(
            restarted,
            "_start_event_service",
            wraps=restarted._start_event_service,
        ) as start_event_service:
            services = await asyncio.gather(
                *[restarted.get_event_service(conversation_info.id) for _ in range(5)]
            )

        assert start_event_service.await_count == 1
        assert services[0] is not None
        assert all(service is services[0] for service in services)


@pytest.mark.asyncio
async def test_status_filters_refresh_unloaded_shared_state(persisted_conversation):
    conversations_dir, conversation_id = persisted_conversation

    async with ConversationService(conversations_dir=conversations_dir) as primary:
        primary_runtime = await primary.get_event_service(conversation_id)
        assert primary_runtime is not None

        async with ConversationService(conversations_dir=conversations_dir) as observer:
            assert observer._event_services == {}
            assert (
                observer._conversation_records[conversation_id].execution_status
                == ConversationExecutionStatus.IDLE
            )

            primary_state = await primary_runtime.get_state()
            primary_state.execution_status = ConversationExecutionStatus.RUNNING
            assert (
                await observer.count_conversations(
                    execution_status=ConversationExecutionStatus.RUNNING
                )
                == 1
            )

            primary_state.execution_status = ConversationExecutionStatus.PAUSED
            page = await observer.search_conversations(
                execution_status=ConversationExecutionStatus.PAUSED
            )
            assert [item.id for item in page.items] == [conversation_id]
            assert observer._event_services == {}


@pytest.mark.asyncio
async def test_waiting_hydration_cannot_restore_deleted_conversation(
    persisted_conversation,
):
    conversations_dir, conversation_id = persisted_conversation

    async with ConversationService(conversations_dir=conversations_dir) as service:
        record = service._conversation_records[conversation_id]
        state = ConversationState(
            id=conversation_id,
            agent=record.stored.agent,
            workspace=record.stored.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=record.stored.confirmation_policy,
        )
        deleting_runtime = AsyncMock(spec=EventService)
        deleting_runtime.is_open.return_value = True
        deleting_runtime.stored = record.stored
        deleting_runtime.get_state.return_value = state
        deleting_runtime.conversation_dir = conversations_dir / conversation_id.hex

        replacement_runtime = AsyncMock(spec=EventService)
        replacement_runtime.stored = record.stored

        async def publish_replacement(_stored: StoredConversation) -> EventService:
            assert service._event_services is not None
            service._event_services[conversation_id] = replacement_runtime
            service._conversation_records[conversation_id] = record
            return replacement_runtime

        with (
            patch.object(
                service,
                "_start_event_service",
                side_effect=publish_replacement,
            ) as start_event_service,
            patch("openhands.agent_server.conversation_service.safe_rmtree"),
        ):
            await service._lifecycle_lock.acquire()
            try:
                getter_task = asyncio.create_task(
                    service.get_event_service(conversation_id)
                )
                await asyncio.sleep(0)
                assert service._event_services is not None
                service._event_services[conversation_id] = deleting_runtime
                delete_task = asyncio.create_task(
                    service.delete_conversation(conversation_id)
                )
                await asyncio.sleep(0)
            finally:
                service._lifecycle_lock.release()

            getter_result, deleted = await asyncio.gather(getter_task, delete_task)

        assert getter_result is deleting_runtime
        assert deleted is True
        start_event_service.assert_not_awaited()
        assert service._event_services is not None
        assert conversation_id not in service._event_services
        assert conversation_id not in service._conversation_records


@pytest.mark.asyncio
async def test_shutdown_closes_runtime_from_in_flight_hydration(
    persisted_conversation,
):
    conversations_dir, conversation_id = persisted_conversation
    service = ConversationService(conversations_dir=conversations_dir)
    await service.__aenter__()
    record = service._conversation_records[conversation_id]
    state = ConversationState(
        id=conversation_id,
        agent=record.stored.agent,
        workspace=record.stored.workspace,
        execution_status=ConversationExecutionStatus.IDLE,
        confirmation_policy=record.stored.confirmation_policy,
    )
    startup_entered = asyncio.Event()
    finish_startup = asyncio.Event()
    runtime = AsyncMock(spec=EventService)
    runtime.stored = record.stored
    runtime.get_state.return_value = state

    async def blocking_start() -> None:
        startup_entered.set()
        await finish_startup.wait()

    async def close_on_exit(*_args) -> None:
        await runtime.close()

    runtime.start.side_effect = blocking_start
    runtime.__aexit__.side_effect = close_on_exit

    try:
        with patch(
            "openhands.agent_server.conversation_service.EventService",
            return_value=runtime,
        ):
            hydration_task = asyncio.create_task(
                service.get_event_service(conversation_id)
            )
            await asyncio.wait_for(startup_entered.wait(), timeout=1)
            shutdown_task = asyncio.create_task(service.__aexit__(None, None, None))
            await asyncio.sleep(0)
            finish_startup.set()
            hydrated_runtime, _ = await asyncio.wait_for(
                asyncio.gather(hydration_task, shutdown_task), timeout=1
            )
    finally:
        finish_startup.set()
        if service._event_services is not None:
            await service.__aexit__(None, None, None)

    assert hydrated_runtime is runtime
    assert service._event_services is None
    assert service._conversation_records == {}
    runtime.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_fork_rejects_id_of_unloaded_persisted_conversation(tmp_path):
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )
    async with ConversationService(conversations_dir=conversations_dir) as primary:
        source, _ = await primary.start_conversation(request)
        target, _ = await primary.start_conversation(request)

    target_meta = conversations_dir / target.id.hex / "meta.json"
    original_meta = target_meta.read_text()
    async with ConversationService(conversations_dir=conversations_dir) as restarted:
        assert restarted._event_services == {}
        with pytest.raises(ValueError, match="already exists"):
            await restarted.fork_conversation(source.id, fork_id=target.id)

    assert target_meta.read_text() == original_meta


class TestConversationServiceSearchConversations:
    """Test cases for ConversationService.search_conversations method."""

    @pytest.mark.asyncio
    async def test_search_conversations_inactive_service(self, conversation_service):
        """Test that search_conversations raises ValueError when service is inactive."""
        conversation_service._event_services = None

        with pytest.raises(ValueError, match="inactive_service"):
            await conversation_service.search_conversations()

    @pytest.mark.asyncio
    async def test_search_conversations_empty_result(self, conversation_service):
        """Test search_conversations with no conversations."""
        result = await conversation_service.search_conversations()

        assert isinstance(result, ConversationPage)
        assert result.items == []
        assert result.next_page_id is None

    @pytest.mark.asyncio
    async def test_search_conversations_basic(
        self, conversation_service, sample_stored_conversation
    ):
        """Test basic search_conversations functionality."""
        # Create mock event service
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        result = await conversation_service.search_conversations()

        assert len(result.items) == 1
        assert result.items[0].id == conversation_id
        assert result.items[0].execution_status == ConversationExecutionStatus.IDLE
        assert result.next_page_id is None

    @pytest.mark.asyncio
    async def test_search_conversations_with_critic_redacts_api_key(
        self, conversation_service
    ):
        """ConversationInfo should serialize critic secrets without rejecting them."""
        agent = Agent(
            llm=LLM(model="gpt-4o", api_key=SecretStr("llm-secret")),
            tools=[],
            critic=APIBasedCritic(
                api_key=SecretStr("critic-secret"),
                server_url="https://critic.example.com",
                model_name="critic",
            ),
        )
        stored_conv = StoredConversation(
            id=uuid4(),
            agent=agent,
            workspace=LocalWorkspace(working_dir="workspace/project"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        )

        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = stored_conv
        mock_service.get_state.return_value = ConversationState(
            id=stored_conv.id,
            agent=stored_conv.agent,
            workspace=stored_conv.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=stored_conv.confirmation_policy,
        )
        conversation_service._event_services[stored_conv.id] = mock_service

        result = await conversation_service.search_conversations()

        info = result.items[0]
        assert isinstance(info.agent.critic, APIBasedCritic)
        assert info.agent.critic.api_key is None

        payload = info.model_dump(mode="json")
        assert payload["agent"]["llm"]["api_key"] is None
        assert payload["agent"]["critic"]["api_key"] is None
        assert "llm-secret" not in str(payload)
        assert "critic-secret" not in str(payload)
        assert "critic-secret" not in str(info)

    @pytest.mark.asyncio
    async def test_search_conversations_status_filter(self, conversation_service):
        """Test filtering conversations by status."""
        # Create multiple conversations with different statuses
        conversations = []
        for i, status in enumerate(
            [
                ConversationExecutionStatus.IDLE,
                ConversationExecutionStatus.RUNNING,
                ConversationExecutionStatus.FINISHED,
            ]
        ):
            stored_conv = StoredConversation(
                id=uuid4(),
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir="workspace/project"),
                confirmation_policy=NeverConfirm(),
                initial_message=None,
                metrics=None,
                created_at=datetime(2025, 1, 1, 12, i, 0, tzinfo=UTC),
                updated_at=datetime(2025, 1, 1, 12, i + 30, 0, tzinfo=UTC),
            )

            mock_service = AsyncMock(spec=EventService)
            mock_service.stored = stored_conv
            mock_state = ConversationState(
                id=stored_conv.id,
                agent=stored_conv.agent,
                workspace=stored_conv.workspace,
                execution_status=status,
                confirmation_policy=stored_conv.confirmation_policy,
            )
            mock_service.get_state.return_value = mock_state

            conversation_service._event_services[stored_conv.id] = mock_service
            conversations.append((stored_conv.id, status))

        # Test filtering by IDLE status
        result = await conversation_service.search_conversations(
            execution_status=ConversationExecutionStatus.IDLE
        )
        assert len(result.items) == 1
        assert result.items[0].execution_status == ConversationExecutionStatus.IDLE

        # Test filtering by RUNNING status
        result = await conversation_service.search_conversations(
            execution_status=ConversationExecutionStatus.RUNNING
        )
        assert len(result.items) == 1
        assert result.items[0].execution_status == ConversationExecutionStatus.RUNNING

        # Test filtering by non-existent status
        result = await conversation_service.search_conversations(
            execution_status=ConversationExecutionStatus.ERROR
        )
        assert len(result.items) == 0

    @pytest.mark.asyncio
    async def test_search_conversations_sorting(self, conversation_service):
        """Test sorting conversations by different criteria."""
        # Create conversations with different timestamps
        conversations = []

        for i in range(3):
            stored_conv = StoredConversation(
                id=uuid4(),
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir="workspace/project"),
                confirmation_policy=NeverConfirm(),
                initial_message=None,
                metrics=None,
                created_at=datetime(
                    2025, 1, i + 1, 12, 0, 0, tzinfo=UTC
                ),  # Different days
                updated_at=datetime(2025, 1, i + 1, 12, 30, 0, tzinfo=UTC),
            )

            mock_service = AsyncMock(spec=EventService)
            mock_service.stored = stored_conv
            mock_state = ConversationState(
                id=stored_conv.id,
                agent=stored_conv.agent,
                workspace=stored_conv.workspace,
                execution_status=ConversationExecutionStatus.IDLE,
                confirmation_policy=stored_conv.confirmation_policy,
            )
            mock_service.get_state.return_value = mock_state

            conversation_service._event_services[stored_conv.id] = mock_service
            conversations.append(stored_conv)

        # Test CREATED_AT (ascending)
        result = await conversation_service.search_conversations(
            sort_order=ConversationSortOrder.CREATED_AT
        )
        assert len(result.items) == 3
        assert (
            result.items[0].created_at
            < result.items[1].created_at
            < result.items[2].created_at
        )

        # Test CREATED_AT_DESC (descending) - default
        result = await conversation_service.search_conversations(
            sort_order=ConversationSortOrder.CREATED_AT_DESC
        )
        assert len(result.items) == 3
        assert (
            result.items[0].created_at
            > result.items[1].created_at
            > result.items[2].created_at
        )

        # Test UPDATED_AT (ascending)
        result = await conversation_service.search_conversations(
            sort_order=ConversationSortOrder.UPDATED_AT
        )
        assert len(result.items) == 3
        assert (
            result.items[0].updated_at
            < result.items[1].updated_at
            < result.items[2].updated_at
        )

        # Test UPDATED_AT_DESC (descending)
        result = await conversation_service.search_conversations(
            sort_order=ConversationSortOrder.UPDATED_AT_DESC
        )
        assert len(result.items) == 3
        assert (
            result.items[0].updated_at
            > result.items[1].updated_at
            > result.items[2].updated_at
        )

    @pytest.mark.asyncio
    async def test_search_conversations_pagination(self, conversation_service):
        """Test pagination functionality."""
        # Create 5 conversations
        conversation_ids = []
        for i in range(5):
            stored_conv = StoredConversation(
                id=uuid4(),
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir="workspace/project"),
                confirmation_policy=NeverConfirm(),
                initial_message=None,
                metrics=None,
                created_at=datetime(2025, 1, 1, 12, i, 0, tzinfo=UTC),
                updated_at=datetime(2025, 1, 1, 12, i + 30, 0, tzinfo=UTC),
            )

            mock_service = AsyncMock(spec=EventService)
            mock_service.stored = stored_conv
            mock_state = ConversationState(
                id=stored_conv.id,
                agent=stored_conv.agent,
                workspace=stored_conv.workspace,
                execution_status=ConversationExecutionStatus.IDLE,
                confirmation_policy=stored_conv.confirmation_policy,
            )
            mock_service.get_state.return_value = mock_state

            conversation_service._event_services[stored_conv.id] = mock_service
            conversation_ids.append(stored_conv.id)

        # Test first page with limit 2
        result = await conversation_service.search_conversations(limit=2)
        assert len(result.items) == 2
        assert result.next_page_id is not None

        # Test second page using next_page_id
        result = await conversation_service.search_conversations(
            page_id=result.next_page_id, limit=2
        )
        assert len(result.items) == 2
        assert result.next_page_id is not None

        # Test last page
        result = await conversation_service.search_conversations(
            page_id=result.next_page_id, limit=2
        )
        assert len(result.items) == 1  # Only one item left
        assert result.next_page_id is None

    @pytest.mark.asyncio
    async def test_search_conversations_combined_filter_and_sort(
        self, conversation_service
    ):
        """Test combining status filtering with sorting."""
        # Create conversations with mixed statuses and timestamps
        conversations_data = [
            (
                ConversationExecutionStatus.IDLE,
                datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            ),
            (
                ConversationExecutionStatus.RUNNING,
                datetime(2025, 1, 2, 12, 0, 0, tzinfo=UTC),
            ),
            (
                ConversationExecutionStatus.IDLE,
                datetime(2025, 1, 3, 12, 0, 0, tzinfo=UTC),
            ),
            (
                ConversationExecutionStatus.FINISHED,
                datetime(2025, 1, 4, 12, 0, 0, tzinfo=UTC),
            ),
        ]

        for status, created_at in conversations_data:
            stored_conv = StoredConversation(
                id=uuid4(),
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir="workspace/project"),
                confirmation_policy=NeverConfirm(),
                initial_message=None,
                metrics=None,
                created_at=created_at,
                updated_at=created_at,
            )

            mock_service = AsyncMock(spec=EventService)
            mock_service.stored = stored_conv
            mock_state = ConversationState(
                id=stored_conv.id,
                agent=stored_conv.agent,
                workspace=stored_conv.workspace,
                execution_status=status,
                confirmation_policy=stored_conv.confirmation_policy,
            )
            mock_service.get_state.return_value = mock_state

            conversation_service._event_services[stored_conv.id] = mock_service

        # Filter by IDLE status and sort by CREATED_AT_DESC
        result = await conversation_service.search_conversations(
            execution_status=ConversationExecutionStatus.IDLE,
            sort_order=ConversationSortOrder.CREATED_AT_DESC,
        )

        assert len(result.items) == 2  # Two IDLE conversations
        # Should be sorted by created_at descending (newest first)
        assert result.items[0].created_at > result.items[1].created_at

    @pytest.mark.asyncio
    async def test_search_conversations_invalid_page_id(
        self, conversation_service, sample_stored_conversation
    ):
        """Test search_conversations with invalid page_id."""
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_service._event_services[sample_stored_conversation.id] = (
            mock_service
        )

        # Use a non-existent page_id
        invalid_page_id = uuid4().hex
        result = await conversation_service.search_conversations(
            page_id=invalid_page_id
        )

        # Should return all items since page_id doesn't match any conversation
        assert len(result.items) == 1
        assert result.next_page_id is None


class TestConversationServiceCountConversations:
    """Test cases for ConversationService.count_conversations method."""

    @pytest.mark.asyncio
    async def test_count_conversations_inactive_service(self, conversation_service):
        """Test that count_conversations raises ValueError when service is inactive."""
        conversation_service._event_services = None

        with pytest.raises(ValueError, match="inactive_service"):
            await conversation_service.count_conversations()

    @pytest.mark.asyncio
    async def test_count_conversations_empty_result(self, conversation_service):
        """Test count_conversations with no conversations."""
        result = await conversation_service.count_conversations()
        assert result == 0

    @pytest.mark.asyncio
    async def test_count_conversations_basic(
        self, conversation_service, sample_stored_conversation
    ):
        """Test basic count_conversations functionality."""
        # Create mock event service
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        result = await conversation_service.count_conversations()
        assert result == 1

    @pytest.mark.asyncio
    async def test_count_conversations_status_filter(self, conversation_service):
        """Test counting conversations with status filter."""
        # Create multiple conversations with different statuses
        statuses = [
            ConversationExecutionStatus.IDLE,
            ConversationExecutionStatus.RUNNING,
            ConversationExecutionStatus.FINISHED,
            ConversationExecutionStatus.IDLE,  # Another IDLE one
        ]

        for i, status in enumerate(statuses):
            stored_conv = StoredConversation(
                id=uuid4(),
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir="workspace/project"),
                confirmation_policy=NeverConfirm(),
                initial_message=None,
                metrics=None,
                created_at=datetime(2025, 1, 1, 12, i, 0, tzinfo=UTC),
                updated_at=datetime(2025, 1, 1, 12, i + 30, 0, tzinfo=UTC),
            )

            mock_service = AsyncMock(spec=EventService)
            mock_service.stored = stored_conv
            mock_state = ConversationState(
                id=stored_conv.id,
                agent=stored_conv.agent,
                workspace=stored_conv.workspace,
                execution_status=status,
                confirmation_policy=stored_conv.confirmation_policy,
            )
            mock_service.get_state.return_value = mock_state

            conversation_service._event_services[stored_conv.id] = mock_service

        # Test counting all conversations
        result = await conversation_service.count_conversations()
        assert result == 4

        # Test counting by IDLE status (should be 2)
        result = await conversation_service.count_conversations(
            execution_status=ConversationExecutionStatus.IDLE
        )
        assert result == 2

        # Test counting by RUNNING status (should be 1)
        result = await conversation_service.count_conversations(
            execution_status=ConversationExecutionStatus.RUNNING
        )
        assert result == 1

        # Test counting by non-existent status (should be 0)
        result = await conversation_service.count_conversations(
            execution_status=ConversationExecutionStatus.ERROR
        )
        assert result == 0

    @pytest.mark.asyncio
    async def test_count_conversations_includes_regular_and_acp(
        self, conversation_service
    ):
        legacy_conversation = StoredConversation(
            id=uuid4(),
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="workspace/project"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        )
        acp_conversation = StoredConversation(
            id=uuid4(),
            agent=ACPAgent(acp_command=["echo", "test"]),
            workspace=LocalWorkspace(working_dir="workspace/project"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 13, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 13, 30, 0, tzinfo=UTC),
        )

        for stored_conv in (legacy_conversation, acp_conversation):
            mock_service = AsyncMock(spec=EventService)
            mock_service.stored = stored_conv
            mock_service.get_state.return_value = ConversationState(
                id=stored_conv.id,
                agent=stored_conv.agent,
                workspace=stored_conv.workspace,
                execution_status=ConversationExecutionStatus.IDLE,
                confirmation_policy=stored_conv.confirmation_policy,
            )
            conversation_service._event_services[stored_conv.id] = mock_service

        assert await conversation_service.count_conversations() == 2


class TestConversationServiceStartConversation:
    """Test cases for ConversationService.start_conversation method."""

    @pytest.mark.asyncio
    async def test_start_conversation_with_secrets(self, conversation_service):
        """Test that secrets are passed to new conversations when starting."""
        # Create test secrets
        test_secrets: dict[str, SecretSource] = {
            "api_key": StaticSecret(value=SecretStr("secret-api-key-123")),
            "database_url": StaticSecret(
                value=SecretStr("postgresql://user:pass@host:5432/db")
            ),
        }

        # Create a start conversation request with secrets
        with tempfile.TemporaryDirectory() as temp_dir:
            request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
                secrets=test_secrets,
            )

            # Mock the EventService constructor and start method
            with patch(
                "openhands.agent_server.conversation_service.EventService"
            ) as mock_event_service_class:
                mock_event_service = AsyncMock(spec=EventService)
                mock_event_service_class.return_value = mock_event_service

                # Mock the state that would be returned
                mock_state = ConversationState(
                    id=uuid4(),
                    agent=request.agent,
                    workspace=request.workspace,
                    execution_status=ConversationExecutionStatus.IDLE,
                    confirmation_policy=request.confirmation_policy,
                )
                mock_event_service.get_state.return_value = mock_state
                mock_event_service.stored = StoredConversation(
                    id=mock_state.id,
                    **request.model_dump(mode="json", context={"expose_secrets": True}),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )

                # Start the conversation
                result, _ = await conversation_service.start_conversation(request)

                # Verify EventService was created with the correct parameters
                mock_event_service_class.assert_called_once()
                call_args = mock_event_service_class.call_args
                stored_conversation = call_args.kwargs["stored"]

                # Verify that secrets were passed to the stored conversation
                assert stored_conversation.secrets == test_secrets
                assert "api_key" in stored_conversation.secrets
                assert "database_url" in stored_conversation.secrets
                assert (
                    stored_conversation.secrets["api_key"].get_value()
                    == "secret-api-key-123"
                )
                assert (
                    stored_conversation.secrets["database_url"].get_value()
                    == "postgresql://user:pass@host:5432/db"
                )

                # Verify the conversation was started
                mock_event_service.start.assert_called_once()

                # Verify the result
                assert result.id == mock_state.id
                assert result.execution_status == ConversationExecutionStatus.IDLE

    @pytest.mark.asyncio
    async def test_start_conversation_without_secrets(self, conversation_service):
        """Test that conversations can be started without secrets."""
        # Create a start conversation request without secrets
        with tempfile.TemporaryDirectory() as temp_dir:
            request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
            )

            # Mock the EventService constructor and start method
            with patch(
                "openhands.agent_server.conversation_service.EventService"
            ) as mock_event_service_class:
                mock_event_service = AsyncMock(spec=EventService)
                mock_event_service_class.return_value = mock_event_service

                # Mock the state that would be returned
                mock_state = ConversationState(
                    id=uuid4(),
                    agent=request.agent,
                    workspace=request.workspace,
                    execution_status=ConversationExecutionStatus.IDLE,
                    confirmation_policy=request.confirmation_policy,
                )
                mock_event_service.get_state.return_value = mock_state
                mock_event_service.stored = StoredConversation(
                    id=mock_state.id,
                    **request.model_dump(mode="json", context={"expose_secrets": True}),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )

                # Start the conversation
                result, _ = await conversation_service.start_conversation(request)

                # Verify EventService was created with the correct parameters
                mock_event_service_class.assert_called_once()
                call_args = mock_event_service_class.call_args
                stored_conversation = call_args.kwargs["stored"]

                # Verify that secrets is an empty dict (default)
                assert stored_conversation.secrets == {}

                # Verify the conversation was started
                mock_event_service.start.assert_called_once()

                # Verify the result
                assert result.id == mock_state.id
                assert result.execution_status == ConversationExecutionStatus.IDLE

    @pytest.mark.asyncio
    async def test_start_conversation_with_worktree_uses_git_worktree(
        self, conversation_service, tmp_path
    ):
        repo_dir = tmp_path / "repo"
        _init_git_repo(repo_dir)
        conversation_id = uuid4()
        worktree_root = tmp_path / "conversation-worktrees"

        request = StartConversationRequest(
            conversation_id=conversation_id,
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir=repo_dir),
            confirmation_policy=NeverConfirm(),
            worktree=True,
        )

        captured: dict[str, StoredConversation] = {}

        def _event_service_factory(**kwargs):
            stored = kwargs["stored"]
            captured["stored"] = stored
            mock_event_service = AsyncMock(spec=EventService)
            mock_event_service.stored = stored
            mock_event_service.get_state.return_value = ConversationState(
                id=stored.id,
                agent=stored.agent,
                workspace=stored.workspace,
                execution_status=ConversationExecutionStatus.IDLE,
                confirmation_policy=stored.confirmation_policy,
            )
            return mock_event_service

        with (
            patch(
                "openhands.agent_server.conversation_service.CONVERSATION_WORKTREE_ROOT",
                worktree_root,
            ),
            patch(
                "openhands.agent_server.conversation_service.EventService",
                side_effect=_event_service_factory,
            ),
        ):
            result, _ = await conversation_service.start_conversation(request)

        stored = captured["stored"]
        expected_worktree = worktree_root / str(conversation_id) / repo_dir.name
        expected_branch = f"openhands/{conversation_id}"

        assert stored.worktree is True
        assert stored.workspace.working_dir == str(expected_worktree)
        assert result.workspace.working_dir == str(expected_worktree)
        assert (expected_worktree / ".git").exists()
        assert (
            run_git_command(
                ["git", "--no-pager", "branch", "--show-current"],
                expected_worktree,
            )
            == expected_branch
        )
        assert stored.agent.agent_context is not None
        suffix = stored.agent.agent_context.system_message_suffix
        assert suffix is not None
        assert str(repo_dir.resolve()) in suffix
        assert str(expected_worktree) in suffix
        assert expected_branch in suffix
        assert "Do all file and git work inside this worktree" in suffix

    @pytest.mark.asyncio
    async def test_start_conversation_with_worktree_preserves_relative_workspace(
        self, conversation_service, tmp_path
    ):
        repo_dir = tmp_path / "repo"
        _init_git_repo(repo_dir)
        workspace_dir = repo_dir / "src" / "pkg"
        workspace_dir.mkdir(parents=True)
        conversation_id = uuid4()
        worktree_root = tmp_path / "conversation-worktrees"

        request = StartConversationRequest(
            conversation_id=conversation_id,
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir=workspace_dir),
            confirmation_policy=NeverConfirm(),
            worktree=True,
        )

        captured: dict[str, StoredConversation] = {}

        def _event_service_factory(**kwargs):
            stored = kwargs["stored"]
            captured["stored"] = stored
            mock_event_service = AsyncMock(spec=EventService)
            mock_event_service.stored = stored
            mock_event_service.get_state.return_value = ConversationState(
                id=stored.id,
                agent=stored.agent,
                workspace=stored.workspace,
                execution_status=ConversationExecutionStatus.IDLE,
                confirmation_policy=stored.confirmation_policy,
            )
            return mock_event_service

        with (
            patch(
                "openhands.agent_server.conversation_service.CONVERSATION_WORKTREE_ROOT",
                worktree_root,
            ),
            patch(
                "openhands.agent_server.conversation_service.EventService",
                side_effect=_event_service_factory,
            ),
        ):
            result, _ = await conversation_service.start_conversation(request)

        stored = captured["stored"]
        expected_worktree = worktree_root / str(conversation_id) / repo_dir.name
        expected_workspace = expected_worktree / "src" / "pkg"

        assert stored.worktree is True
        assert stored.workspace.working_dir == str(expected_workspace)
        assert result.workspace.working_dir == str(expected_workspace)
        assert (expected_worktree / ".git").exists()

    @pytest.mark.asyncio
    async def test_start_conversation_with_worktree_ignores_non_git_workspace(
        self, conversation_service, tmp_path
    ):
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        conversation_id = uuid4()
        worktree_root = tmp_path / "conversation-worktrees"

        request = StartConversationRequest(
            conversation_id=conversation_id,
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir=workspace_dir),
            confirmation_policy=NeverConfirm(),
            worktree=True,
        )

        captured: dict[str, StoredConversation] = {}

        def _event_service_factory(**kwargs):
            stored = kwargs["stored"]
            captured["stored"] = stored
            mock_event_service = AsyncMock(spec=EventService)
            mock_event_service.stored = stored
            mock_event_service.get_state.return_value = ConversationState(
                id=stored.id,
                agent=stored.agent,
                workspace=stored.workspace,
                execution_status=ConversationExecutionStatus.IDLE,
                confirmation_policy=stored.confirmation_policy,
            )
            return mock_event_service

        with (
            patch(
                "openhands.agent_server.conversation_service.CONVERSATION_WORKTREE_ROOT",
                worktree_root,
            ),
            patch(
                "openhands.agent_server.conversation_service.EventService",
                side_effect=_event_service_factory,
            ),
        ):
            result, _ = await conversation_service.start_conversation(request)

        stored = captured["stored"]

        assert stored.worktree is True
        assert stored.workspace.working_dir == str(workspace_dir)
        assert result.workspace.working_dir == str(workspace_dir)
        assert stored.agent.agent_context is None
        assert not (worktree_root / str(conversation_id)).exists()

    def test_get_worktree_start_point_prefers_origin_default_branch(self, tmp_path):
        """With an ``origin`` remote, fetch first and return ``origin/<default>``.

        Local ``main``/``master`` should not influence the choice when a remote
        default branch is available.
        """
        upstream = tmp_path / "upstream.git"
        run_git_command(["git", "init", "--bare", "-b", "trunk", str(upstream)])

        repo_dir = tmp_path / "repo"
        _init_git_repo(repo_dir)
        # Rename the local default to "trunk" and publish it so origin/HEAD
        # resolves to origin/trunk (not main/master).
        run_git_command(["git", "branch", "-m", "main", "trunk"], repo_dir)
        run_git_command(
            ["git", "remote", "add", "origin", str(upstream)],
            repo_dir,
        )
        run_git_command(["git", "push", "-u", "origin", "trunk"], repo_dir)
        run_git_command(
            ["git", "remote", "set-head", "origin", "trunk"],
            repo_dir,
        )
        # Create a local "main" branch that we expect to be IGNORED in favor of
        # the remote default, so this test fails if we silently fall through.
        run_git_command(["git", "branch", "main"], repo_dir)

        # Add a new upstream commit; the start point must reflect this commit,
        # proving we fetched before resolving.
        clone_dir = tmp_path / "publisher"
        run_git_command(
            ["git", "clone", str(upstream), str(clone_dir)],
        )
        (clone_dir / "remote.txt").write_text("remote\n")
        run_git_command(["git", "add", "remote.txt"], clone_dir)
        run_git_command(
            [
                "git",
                "-c",
                "user.name=OpenHands Test",
                "-c",
                "user.email=openhands@example.com",
                "commit",
                "-m",
                "remote update",
            ],
            clone_dir,
        )
        run_git_command(["git", "push", "origin", "trunk"], clone_dir)
        remote_tip = run_git_command(
            ["git", "--no-pager", "rev-parse", "trunk"], clone_dir
        )

        start_point = _get_worktree_start_point(repo_dir)

        assert start_point == "origin/trunk"
        resolved = run_git_command(
            ["git", "--no-pager", "rev-parse", start_point], repo_dir
        )
        assert resolved == remote_tip

    def test_get_worktree_start_point_falls_back_to_local_main(self, tmp_path):
        """No ``origin`` remote → fall back to local ``main``."""
        repo_dir = tmp_path / "repo"
        _init_git_repo(repo_dir)  # creates local "main"
        # Move HEAD off main so we prove main is selected by policy, not because
        # it happens to be the current branch.
        run_git_command(["git", "checkout", "-b", "feature/x"], repo_dir)

        assert _get_worktree_start_point(repo_dir) == "main"

    def test_get_worktree_start_point_falls_back_to_master(self, tmp_path):
        """No remote and no local ``main`` → fall back to local ``master``."""
        repo_dir = tmp_path / "repo"
        _init_git_repo(repo_dir)
        run_git_command(["git", "branch", "-m", "main", "master"], repo_dir)
        # Detach so neither main nor master is the current branch.
        run_git_command(["git", "checkout", "--detach"], repo_dir)

        assert _get_worktree_start_point(repo_dir) == "master"

    def test_get_worktree_start_point_tolerates_fetch_failure(self, tmp_path):
        """If ``git fetch origin`` fails, fall back to cached refs.

        Simulate an unreachable remote by pointing ``origin`` at a non-existent
        path; we still expect to resolve to ``origin/<default>`` using cached
        refs that were set up before the remote URL was broken.
        """
        upstream = tmp_path / "upstream.git"
        run_git_command(["git", "init", "--bare", "-b", "main", str(upstream)])

        repo_dir = tmp_path / "repo"
        _init_git_repo(repo_dir)
        run_git_command(
            ["git", "remote", "add", "origin", str(upstream)],
            repo_dir,
        )
        run_git_command(["git", "push", "-u", "origin", "main"], repo_dir)
        run_git_command(
            ["git", "remote", "set-head", "origin", "main"],
            repo_dir,
        )
        # Break the remote URL so fetch fails, but origin/HEAD is still cached.
        run_git_command(
            ["git", "remote", "set-url", "origin", str(tmp_path / "does-not-exist")],
            repo_dir,
        )

        assert _get_worktree_start_point(repo_dir) == "origin/main"

    @pytest.mark.asyncio
    async def test_start_conversation_with_custom_id(self, conversation_service):
        """Test that conversations can be started with a custom conversation_id."""
        custom_id = uuid4()

        # Create a start conversation request with custom conversation_id
        with tempfile.TemporaryDirectory() as temp_dir:
            request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
                conversation_id=custom_id,
            )

            result, is_new = await conversation_service.start_conversation(request)
            assert result.id == custom_id
            assert is_new

    @pytest.mark.asyncio
    async def test_start_conversation_with_duplicate_id(self, conversation_service):
        """Test duplicate conversation ids are detected."""
        custom_id = uuid4()

        # Create a start conversation request with custom conversation_id
        with tempfile.TemporaryDirectory() as temp_dir:
            request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
                conversation_id=custom_id,
            )

            result, is_new = await conversation_service.start_conversation(request)
            assert result.id == custom_id
            assert is_new

            duplicate_request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
                conversation_id=custom_id,
            )

            result, is_new = await conversation_service.start_conversation(
                duplicate_request
            )
            assert result.id == custom_id
            assert not is_new

    @pytest.mark.asyncio
    async def test_start_conversation_reuse_checks_is_open(self, conversation_service):
        """Test that conversation reuse checks if event service is open."""
        custom_id = uuid4()

        # Create a mock event service that exists but is not open
        mock_event_service = AsyncMock(spec=EventService)
        mock_event_service.is_open.return_value = False
        mock_event_service.stored = StoredConversation(
            id=custom_id,
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="workspace/project"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        )
        conversation_service._event_services[custom_id] = mock_event_service

        with tempfile.TemporaryDirectory() as temp_dir:
            request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
                conversation_id=custom_id,
            )

            # Mock the _start_event_service method to avoid actual startup
            with patch.object(
                conversation_service, "_start_event_service"
            ) as mock_start:
                mock_new_service = AsyncMock(spec=EventService)
                mock_new_service.stored = StoredConversation(
                    id=custom_id,
                    agent=request.agent,
                    workspace=request.workspace,
                    confirmation_policy=request.confirmation_policy,
                    initial_message=request.initial_message,
                    metrics=None,
                    created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
                    updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
                )
                mock_state = ConversationState(
                    id=custom_id,
                    agent=request.agent,
                    workspace=request.workspace,
                    execution_status=ConversationExecutionStatus.IDLE,
                    confirmation_policy=request.confirmation_policy,
                )
                mock_new_service.get_state.return_value = mock_state
                mock_start.return_value = mock_new_service

                result, is_new = await conversation_service.start_conversation(request)

                # Should create a new conversation since existing one is not open
                assert result.id == custom_id
                assert is_new
                mock_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_conversation_reuse_when_open(self, conversation_service):
        """Test that conversation is reused when event service is open."""
        custom_id = uuid4()

        # Create a mock event service that exists and is open
        mock_event_service = AsyncMock(spec=EventService)
        mock_event_service.is_open.return_value = True
        mock_event_service.stored = StoredConversation(
            id=custom_id,
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="workspace/project"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        )
        mock_state = ConversationState(
            id=custom_id,
            agent=mock_event_service.stored.agent,
            workspace=mock_event_service.stored.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=mock_event_service.stored.confirmation_policy,
        )
        mock_event_service.get_state.return_value = mock_state
        conversation_service._event_services[custom_id] = mock_event_service

        with tempfile.TemporaryDirectory() as temp_dir:
            request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
                conversation_id=custom_id,
            )

            # Mock the _start_event_service method to ensure it's not called
            with patch.object(
                conversation_service, "_start_event_service"
            ) as mock_start:
                result, is_new = await conversation_service.start_conversation(request)

                # Should reuse existing conversation since it's open
                assert result.id == custom_id
                assert not is_new
                mock_start.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_conversation_returns_existing_acp_conversation(
        self, conversation_service
    ):
        custom_id = uuid4()
        stored = StoredConversation(
            id=custom_id,
            agent=ACPAgent(acp_command=["echo", "test"]),
            workspace=LocalWorkspace(working_dir="workspace/project"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        )
        mock_event_service = AsyncMock(spec=EventService)
        mock_event_service.is_open.return_value = True
        mock_event_service.stored = stored
        mock_event_service.get_state.return_value = ConversationState(
            id=stored.id,
            agent=stored.agent,
            workspace=stored.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=stored.confirmation_policy,
        )
        conversation_service._event_services[custom_id] = mock_event_service

        with tempfile.TemporaryDirectory() as temp_dir:
            request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
                conversation_id=custom_id,
            )

            # Reattaching by conversation_id returns the stored conversation contract
            # so callers can resume ACP conversations through the unified endpoint
            # even if the new request carries a regular Agent config.
            with patch.object(
                conversation_service, "_start_event_service"
            ) as mock_start:
                (
                    conversation_info,
                    is_new,
                ) = await conversation_service.start_conversation(request)

                assert is_new is False
                assert isinstance(conversation_info, ACPConversationInfo)
                assert conversation_info.agent.kind == "ACPAgent"
                mock_start.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_event_service_failure_cleanup(self, conversation_service):
        """Test that event service is cleaned up when startup fails."""
        with tempfile.TemporaryDirectory() as temp_dir:
            stored = StoredConversation(
                id=uuid4(),
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
                initial_message=None,
                metrics=None,
                created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
                updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
            )

            # Mock EventService to simulate startup failure
            with patch(
                "openhands.agent_server.conversation_service.EventService"
            ) as mock_event_service_class:
                mock_event_service = AsyncMock()
                mock_event_service.start.side_effect = Exception("Startup failed")
                mock_event_service.close = AsyncMock()
                mock_event_service_class.return_value = mock_event_service

                # Attempt to start event service should fail and clean up
                with pytest.raises(Exception, match="Startup failed"):
                    await conversation_service._start_event_service(stored)

                # Verify cleanup was called
                mock_event_service.close.assert_called_once()

                # Verify event service was not stored
                assert stored.id not in conversation_service._event_services

    @pytest.mark.asyncio
    async def test_start_event_service_success_stores_service(
        self, conversation_service
    ):
        """Test that event service is stored only after successful startup."""
        with tempfile.TemporaryDirectory() as temp_dir:
            stored = StoredConversation(
                id=uuid4(),
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
                initial_message=None,
                metrics=None,
                created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
                updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
            )

            # Mock EventService to simulate successful startup
            with patch(
                "openhands.agent_server.conversation_service.EventService"
            ) as mock_event_service_class:
                mock_event_service = AsyncMock()
                mock_event_service.start = AsyncMock()  # Successful startup
                mock_event_service_class.return_value = mock_event_service

                # Start event service should succeed
                result = await conversation_service._start_event_service(stored)

                # Verify startup was called
                mock_event_service.start.assert_called_once()

                # Verify event service was stored after successful startup
                assert stored.id in conversation_service._event_services
                assert (
                    conversation_service._event_services[stored.id]
                    == mock_event_service
                )
                assert result == mock_event_service


class TestConversationServiceUpdateConversation:
    """Test cases for ConversationService.update_conversation method."""

    @pytest.mark.asyncio
    async def test_update_conversation_success(
        self, conversation_service, sample_stored_conversation
    ):
        """Test successful update of conversation title."""
        # Create mock event service
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        # Update the title
        new_title = "My Updated Conversation Title"
        request = UpdateConversationRequest(title=new_title)
        result = await conversation_service.update_conversation(
            conversation_id, request
        )

        # Verify update was successful
        assert result is True
        assert mock_service.stored.title == new_title
        mock_service.save_meta.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_conversation_strips_whitespace(
        self, conversation_service, sample_stored_conversation
    ):
        """Test that update_conversation strips leading/trailing whitespace."""
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        # Update with title that has whitespace
        new_title = "   Whitespace Test   "
        request = UpdateConversationRequest(title=new_title)
        result = await conversation_service.update_conversation(
            conversation_id, request
        )

        # Verify whitespace was stripped
        assert result is True
        assert mock_service.stored.title == "Whitespace Test"
        mock_service.save_meta.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_conversation_tags_uses_state_lock(
        self, conversation_service, sample_stored_conversation
    ):
        """Test that tag updates hold the ConversationState lock."""
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        acquire_spy = MagicMock(wraps=mock_state._lock.acquire)
        release_spy = MagicMock(wraps=mock_state._lock.release)
        mock_state._lock.acquire = acquire_spy
        mock_state._lock.release = release_spy
        mock_service.get_state.return_value = mock_state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        request = UpdateConversationRequest(tags={"env": "prod"})
        result = await conversation_service.update_conversation(
            conversation_id, request
        )

        assert result is True
        assert mock_service.stored.tags == {"env": "prod"}
        assert mock_state.tags == {"env": "prod"}
        assert acquire_spy.call_count >= 2
        assert release_spy.call_count == acquire_spy.call_count

    @pytest.mark.asyncio
    async def test_update_conversation_tags_wait_does_not_block_event_loop(
        self, conversation_service, sample_stored_conversation
    ):
        """Waiting on the state lock must not stall unrelated async work."""
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        lock_acquired = threading.Event()
        release_lock = threading.Event()
        timings: dict[str, float] = {}

        def hold_state_lock() -> None:
            with state:
                timings["lock_start"] = time.monotonic()
                lock_acquired.set()
                release_lock.wait(timeout=1.0)
                timings["lock_end"] = time.monotonic()

        holder = threading.Thread(target=hold_state_lock, daemon=True)
        holder.start()
        assert lock_acquired.wait(timeout=1.0)

        async def heartbeat() -> None:
            await asyncio.sleep(0.05)
            timings["heartbeat"] = time.monotonic()

        async def release_after_delay() -> None:
            await asyncio.sleep(0.2)
            release_lock.set()

        with patch.object(
            conversation_service, "_notify_conversation_webhooks", new=AsyncMock()
        ):
            await asyncio.wait_for(
                asyncio.gather(
                    conversation_service.update_conversation(
                        conversation_id,
                        UpdateConversationRequest(tags={"env": "prod"}),
                    ),
                    heartbeat(),
                    release_after_delay(),
                ),
                timeout=1.0,
            )

        holder.join(timeout=1.0)
        assert not holder.is_alive()
        assert mock_service.stored.tags == {"env": "prod"}
        assert state.tags == {"env": "prod"}
        assert timings["heartbeat"] < timings["lock_end"], (
            "update_conversation blocked the async loop while waiting for the "
            "state lock"
        )

    @pytest.mark.asyncio
    async def test_update_conversation_not_found(self, conversation_service):
        """Test updating a non-existent conversation returns False."""
        non_existent_id = uuid4()
        request = UpdateConversationRequest(title="New Title")
        result = await conversation_service.update_conversation(
            non_existent_id, request
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_update_conversation_inactive_service(self, conversation_service):
        """Test that update_conversation raises ValueError when service is inactive."""
        conversation_service._event_services = None

        request = UpdateConversationRequest(title="New Title")
        with pytest.raises(ValueError, match="inactive_service"):
            await conversation_service.update_conversation(uuid4(), request)

    @pytest.mark.asyncio
    async def test_update_conversation_notifies_webhooks(
        self, conversation_service, sample_stored_conversation
    ):
        """Test that updating a conversation triggers webhook notifications."""
        # Create mock event service
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        # Mock webhook notification
        with patch.object(
            conversation_service, "_notify_conversation_webhooks", new=AsyncMock()
        ) as mock_notify:
            new_title = "Updated Title for Webhook Test"
            request = UpdateConversationRequest(title=new_title)
            result = await conversation_service.update_conversation(
                conversation_id, request
            )

            # Verify webhook was called
            assert result is True
            mock_notify.assert_called_once()
            # Verify the conversation info passed to webhook has the updated title
            call_args = mock_notify.call_args[0]
            conversation_info = call_args[0]
            assert conversation_info.title == new_title
            assert isinstance(conversation_info, ConversationInfo)

    @pytest.mark.asyncio
    async def test_update_acp_conversation_notifies_webhooks_with_acp_shape(
        self, conversation_service
    ):
        stored_conversation = StoredConversation(
            id=uuid4(),
            agent=ACPAgent(acp_command=["echo", "test"]),
            workspace=LocalWorkspace(working_dir="workspace/project"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        )
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = stored_conversation
        mock_state = ConversationState(
            id=stored_conversation.id,
            agent=stored_conversation.agent,
            workspace=stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_id = stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        with patch.object(
            conversation_service, "_notify_conversation_webhooks", new=AsyncMock()
        ) as mock_notify:
            result = await conversation_service.update_conversation(
                conversation_id, UpdateConversationRequest(title="ACP Title")
            )

            assert result is True
            mock_notify.assert_called_once()
            conversation_info = mock_notify.call_args[0][0]
            assert isinstance(conversation_info, ACPConversationInfo)
            assert conversation_info.agent.kind == "ACPAgent"

    @pytest.mark.asyncio
    async def test_update_conversation_persists_changes(
        self, conversation_service, sample_stored_conversation
    ):
        """Test that title changes are persisted to disk."""
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        # Initial title should be None
        assert mock_service.stored.title is None

        # Update the title
        new_title = "Persisted Title"
        request = UpdateConversationRequest(title=new_title)
        await conversation_service.update_conversation(conversation_id, request)

        # Verify save_meta was called to persist changes
        mock_service.save_meta.assert_called_once()
        # Verify the stored conversation has the new title
        assert mock_service.stored.title == new_title

    @pytest.mark.asyncio
    async def test_update_conversation_multiple_times(
        self, conversation_service, sample_stored_conversation
    ):
        """Test updating the same conversation multiple times."""
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        # First update
        request1 = UpdateConversationRequest(title="First Title")
        result1 = await conversation_service.update_conversation(
            conversation_id, request1
        )
        assert result1 is True
        assert mock_service.stored.title == "First Title"

        # Second update
        request2 = UpdateConversationRequest(title="Second Title")
        result2 = await conversation_service.update_conversation(
            conversation_id, request2
        )
        assert result2 is True
        assert mock_service.stored.title == "Second Title"

        # Third update
        request3 = UpdateConversationRequest(title="Third Title")
        result3 = await conversation_service.update_conversation(
            conversation_id, request3
        )
        assert result3 is True
        assert mock_service.stored.title == "Third Title"

        # Verify save_meta was called three times
        assert mock_service.save_meta.call_count == 3

    @pytest.mark.asyncio
    async def test_update_conversation_sets_updated_at(
        self, conversation_service, sample_stored_conversation
    ):
        """Test that update_conversation advances updated_at.

        Renaming a conversation is a meaningful change; the timestamp must
        reflect when it happened rather than staying at the value set at
        conversation creation time.
        """
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        original_updated_at = mock_service.stored.updated_at

        request = UpdateConversationRequest(title="New Title")
        await conversation_service.update_conversation(conversation_id, request)

        assert mock_service.stored.updated_at > original_updated_at


class TestConversationServiceDeleteConversation:
    """Test cases for ConversationService.delete_conversation method."""

    @pytest.mark.asyncio
    async def test_delete_conversation_inactive_service(self, conversation_service):
        """Test that delete_conversation raises ValueError when service is inactive."""
        conversation_service._event_services = None

        with pytest.raises(ValueError, match="inactive_service"):
            await conversation_service.delete_conversation(uuid4())

    @pytest.mark.asyncio
    async def test_delete_conversation_not_found(self, conversation_service):
        """Test delete_conversation with non-existent conversation ID."""
        result = await conversation_service.delete_conversation(uuid4())
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_conversation_success(self, conversation_service):
        """Test successful conversation deletion."""
        conversation_id = uuid4()

        # Create mock event service
        mock_service = AsyncMock(spec=EventService)
        mock_service.conversation_dir = "/tmp/test_conversation"
        mock_service.stored = StoredConversation(
            id=conversation_id,
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test_workspace"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        )
        mock_state = ConversationState(
            id=conversation_id,
            agent=mock_service.stored.agent,
            workspace=mock_service.stored.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=mock_service.stored.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        # Add to service
        conversation_service._event_services[conversation_id] = mock_service

        # Mock the directory removal to avoid actual filesystem operations
        with patch(
            "openhands.agent_server.conversation_service.safe_rmtree"
        ) as mock_rmtree:
            mock_rmtree.return_value = True

            result = await conversation_service.delete_conversation(conversation_id)

            assert result is True
            assert conversation_id not in conversation_service._event_services

            # Verify event service was closed
            mock_service.close.assert_called_once()

            # Verify directories were removed
            assert mock_rmtree.call_count == 1
            mock_rmtree.assert_any_call(
                "/tmp/test_conversation",
                "conversation directory for " + str(conversation_id),
            )

    @pytest.mark.asyncio
    async def test_delete_conversation_notifies_webhooks_with_deleting_status(
        self, conversation_service, sample_stored_conversation
    ):
        """Test that deleting a conversation triggers webhook notifications.

        Verifies that the webhook receives a conversation info with execution_status
        set to 'deleting' when delete_conversation is called.
        """
        # Create mock event service
        mock_service = AsyncMock(spec=EventService)
        mock_service.conversation_dir = "/tmp/test_conversation"
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        # Mock webhook notification
        with patch.object(
            conversation_service, "_notify_conversation_webhooks", new=AsyncMock()
        ) as mock_notify:
            # Mock the directory removal
            with patch(
                "openhands.agent_server.conversation_service.safe_rmtree"
            ) as mock_rmtree:
                mock_rmtree.return_value = True

                result = await conversation_service.delete_conversation(conversation_id)

                # Verify deletion succeeded
                assert result is True
                assert conversation_id not in conversation_service._event_services

                # Verify webhook was called
                mock_notify.assert_called_once()

                # Verify the conversation info passed to webhook has 'deleting' status
                call_args = mock_notify.call_args[0]
                conversation_info = call_args[0]
                assert (
                    conversation_info.execution_status
                    == ConversationExecutionStatus.DELETING
                )
                assert isinstance(conversation_info, ConversationInfo)

                # Verify event service was closed
                mock_service.close.assert_called_once()

                # Verify directories were removed
                assert mock_rmtree.call_count == 1

    @pytest.mark.asyncio
    async def test_delete_conversation_webhook_failure(self, conversation_service):
        """Test delete_conversation continues when webhook notification fails."""
        conversation_id = uuid4()

        # Create mock event service
        mock_service = AsyncMock(spec=EventService)
        mock_service.conversation_dir = "/tmp/test_conversation"
        mock_service.stored = StoredConversation(
            id=conversation_id,
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test_workspace"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        )

        # Make get_state raise an exception to simulate webhook failure
        mock_service.get_state.side_effect = Exception("Webhook notification failed")

        # Add to service
        conversation_service._event_services[conversation_id] = mock_service

        # Mock the directory removal
        with patch(
            "openhands.agent_server.conversation_service.safe_rmtree"
        ) as mock_rmtree:
            mock_rmtree.return_value = True

            result = await conversation_service.delete_conversation(conversation_id)

            # Should still succeed despite webhook failure
            assert result is True
            assert conversation_id not in conversation_service._event_services

            # Verify event service was still closed
            mock_service.close.assert_called_once()

            # Verify directories were still removed
            assert mock_rmtree.call_count == 1

    @pytest.mark.asyncio
    async def test_delete_conversation_close_failure(self, conversation_service):
        """Test delete_conversation continues when event service close fails."""
        conversation_id = uuid4()

        # Create mock event service
        mock_service = AsyncMock(spec=EventService)
        mock_service.conversation_dir = "/tmp/test_conversation"
        mock_service.stored = StoredConversation(
            id=conversation_id,
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test_workspace"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        )
        mock_state = ConversationState(
            id=conversation_id,
            agent=mock_service.stored.agent,
            workspace=mock_service.stored.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=mock_service.stored.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        # Make close raise an exception
        mock_service.close.side_effect = Exception("Close failed")

        # Add to service
        conversation_service._event_services[conversation_id] = mock_service

        # Mock the directory removal
        with patch(
            "openhands.agent_server.conversation_service.safe_rmtree"
        ) as mock_rmtree:
            mock_rmtree.return_value = True

            result = await conversation_service.delete_conversation(conversation_id)

            # Should still succeed despite close failure
            assert result is True
            assert conversation_id not in conversation_service._event_services

            # Verify directories were still removed
            assert mock_rmtree.call_count == 1

    @pytest.mark.asyncio
    async def test_delete_conversation_retains_retryable_credential_close(
        self, conversation_service, tmp_path
    ):
        conversation_id = uuid4()
        conversation_dir = tmp_path / conversation_id.hex
        conversation_dir.mkdir()
        mock_service = AsyncMock(spec=EventService)
        mock_service.conversation_dir = conversation_dir
        mock_service.stored = StoredConversation(
            id=conversation_id,
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
            confirmation_policy=NeverConfirm(),
        )
        mock_service.get_state.return_value = ConversationState(
            id=conversation_id,
            agent=mock_service.stored.agent,
            workspace=mock_service.stored.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=mock_service.stored.confirmation_policy,
        )
        mock_service.close.side_effect = CredentialSyncError("broker unavailable")
        binding = MagicMock()
        conversation_service._event_services[conversation_id] = mock_service
        conversation_service._conversation_records[conversation_id] = MagicMock()
        conversation_service._credential_bindings[conversation_id] = {
            "CODEX_AUTH_JSON": binding
        }

        with (
            patch(
                "openhands.agent_server.conversation_service.safe_rmtree"
            ) as mock_rmtree,
            pytest.raises(CredentialSyncError, match="broker unavailable"),
        ):
            await conversation_service.delete_conversation(conversation_id)

        assert conversation_service._event_services[conversation_id] is mock_service
        assert conversation_id in conversation_service._conversation_records
        assert conversation_service._credential_bindings[conversation_id] == {
            "CODEX_AUTH_JSON": binding
        }
        assert conversation_dir.exists()
        mock_rmtree.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_conversation_directory_removal_failure(
        self, conversation_service
    ):
        """Test delete_conversation succeeds even when directory removal fails."""
        conversation_id = uuid4()

        # Create mock event service
        mock_service = AsyncMock(spec=EventService)
        mock_service.conversation_dir = "/tmp/test_conversation"
        mock_service.stored = StoredConversation(
            id=conversation_id,
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test_workspace"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        )
        mock_state = ConversationState(
            id=conversation_id,
            agent=mock_service.stored.agent,
            workspace=mock_service.stored.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=mock_service.stored.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        # Add to service
        conversation_service._event_services[conversation_id] = mock_service

        # Mock directory removal to fail (simulating permission errors)
        with patch(
            "openhands.agent_server.conversation_service.safe_rmtree"
        ) as mock_rmtree:
            mock_rmtree.return_value = False  # Simulate removal failure

            result = await conversation_service.delete_conversation(conversation_id)

            # Should still succeed - conversation is removed from tracking
            assert result is True
            assert conversation_id not in conversation_service._event_services

            # Verify event service was closed
            mock_service.close.assert_called_once()

            # Verify removal was attempted
            assert mock_rmtree.call_count == 1


class TestSafeRmtree:
    """Test cases for the _safe_rmtree helper function."""

    def test_safe_rmtree_nonexistent_path(self):
        """Test _safe_rmtree with non-existent path."""
        result = _safe_rmtree("/nonexistent/path", "test directory")
        assert result is True

    def test_safe_rmtree_empty_path(self):
        """Test _safe_rmtree with empty path."""
        result = _safe_rmtree("", "test directory")
        assert result is True

        result = _safe_rmtree(None, "test directory")
        assert result is True

    def test_safe_rmtree_success(self):
        """Test successful directory removal."""
        with tempfile.TemporaryDirectory() as temp_dir:
            test_dir = Path(temp_dir) / "test_subdir"
            test_dir.mkdir()

            # Create a test file
            test_file = test_dir / "test.txt"
            test_file.write_text("test content")

            result = _safe_rmtree(str(test_dir), "test directory")
            assert result is True
            assert not test_dir.exists()

    def test_safe_rmtree_permission_error(self):
        """Test _safe_rmtree handles permission errors gracefully."""
        with patch("shutil.rmtree") as mock_rmtree:
            mock_rmtree.side_effect = PermissionError("Permission denied")

            with patch("os.path.exists", return_value=True):
                result = _safe_rmtree("/test/path", "test directory")
                assert result is False

    def test_safe_rmtree_os_error(self):
        """Test _safe_rmtree handles OS errors gracefully."""
        with patch("shutil.rmtree") as mock_rmtree:
            mock_rmtree.side_effect = OSError("OS error")

            with patch("os.path.exists", return_value=True):
                result = _safe_rmtree("/test/path", "test directory")
                assert result is False

    def test_safe_rmtree_unexpected_error(self):
        """Test _safe_rmtree handles unexpected errors gracefully."""
        with patch("shutil.rmtree") as mock_rmtree:
            mock_rmtree.side_effect = ValueError("Unexpected error")

            with patch("os.path.exists", return_value=True):
                result = _safe_rmtree("/test/path", "test directory")
                assert result is False

    def test_safe_rmtree_readonly_file_handling(self):
        """Test _safe_rmtree handles read-only files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            test_dir = Path(temp_dir) / "test_subdir"
            test_dir.mkdir()

            # Create a test file and make it read-only
            test_file = test_dir / "readonly.txt"
            test_file.write_text("readonly content")
            test_file.chmod(0o444)  # Read-only

            result = _safe_rmtree(str(test_dir), "test directory")
            assert result is True
            assert not test_dir.exists()


class TestAutoTitle:
    """Tests for AutoTitleSubscriber."""

    _GENERATE_TITLE_PATH = (
        "openhands.agent_server.conversation_service.generate_title_from_message"
    )

    def _make_service(
        self,
        title: str | None = None,
        title_llm_profile: str | None = None,
        llm_model: str = "gpt-4o",
        llm_usage_id: str = "test-llm",
    ) -> AsyncMock:
        stored = StoredConversation(
            id=uuid4(),
            agent=Agent(llm=LLM(model=llm_model, usage_id=llm_usage_id), tools=[]),
            workspace=LocalWorkspace(working_dir="workspace/project"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            title=title,
            title_llm_profile=title_llm_profile,
        )
        service = AsyncMock(spec=EventService)
        service.stored = stored

        mock_conversation = MagicMock()
        mock_conversation.agent.llm = stored.agent.llm
        service._conversation = mock_conversation
        return service

    def _user_message_event(self, text: str = "Fix the login bug") -> MessageEvent:
        from openhands.sdk.llm.message import TextContent

        return MessageEvent(
            id="evt-1",
            source="user",
            llm_message=Message(role="user", content=[TextContent(text=text)]),
        )

    @staticmethod
    async def _drain_title_task(
        predicate=lambda: True, max_iterations: int = 50, step: float = 0.02
    ) -> None:
        """Yield to the event loop until the background title task completes.

        `AutoTitleSubscriber` schedules generation via `run_in_executor`, so a
        single `await asyncio.sleep(0)` is not enough to let the executor
        thread finish. Poll with a short sleep until `predicate()` becomes
        truthy or the timeout elapses.
        """
        for _ in range(max_iterations):
            await asyncio.sleep(step)
            if predicate():
                return

    @pytest.mark.asyncio
    async def test_autotitle_sets_title_on_first_user_message(self):
        """Title is generated and saved when the first user message arrives."""
        service = self._make_service()

        with patch(self._GENERATE_TITLE_PATH, return_value="✨ Generated Title"):
            subscriber = AutoTitleSubscriber(service=service)
            await subscriber(self._user_message_event())
            await self._drain_title_task(lambda: service.stored.title is not None)

        assert service.stored.title == "✨ Generated Title"
        service.save_meta.assert_called_once()

    @pytest.mark.asyncio
    async def test_autotitle_skips_non_user_events(self):
        """Non-user events do not trigger title generation.

        Covers ConversationStateUpdateEvent and assistant MessageEvents.
        """
        service = self._make_service()
        subscriber = AutoTitleSubscriber(service=service)

        # ConversationStateUpdateEvent should be ignored
        await subscriber(
            ConversationStateUpdateEvent(key="execution_status", value="IDLE")
        )
        # Assistant MessageEvent should be ignored
        await subscriber(
            MessageEvent(
                id="evt-2", source="agent", llm_message=Message(role="assistant")
            )
        )

        await asyncio.sleep(0)
        assert service.stored.title is None

    @pytest.mark.asyncio
    async def test_autotitle_skips_when_title_already_set(self):
        """No LLM call is made when the conversation already has a title."""
        service = self._make_service(title="Existing Title")
        subscriber = AutoTitleSubscriber(service=service)

        with patch(self._GENERATE_TITLE_PATH) as mock_generate_title:
            await subscriber(self._user_message_event())
            await asyncio.sleep(0)
            mock_generate_title.assert_not_called()

        assert service.stored.title == "Existing Title"

    @pytest.mark.asyncio
    async def test_autotitle_handles_generate_title_failure(self):
        """A failed title generation is logged as a warning and not re-raised."""
        service = self._make_service()

        with patch(self._GENERATE_TITLE_PATH, side_effect=Exception("LLM unavailable")):
            subscriber = AutoTitleSubscriber(service=service)
            # Should not raise
            await subscriber(self._user_message_event())
            await asyncio.sleep(0)

        # Title remains unset; save_meta was never called
        assert service.stored.title is None
        service.save_meta.assert_not_called()

    @pytest.mark.asyncio
    async def test_autotitle_skips_empty_message(self):
        """No title generation if the user message has no text content."""
        service = self._make_service()
        event = MessageEvent(
            id="evt-1", source="user", llm_message=Message(role="user")
        )

        with patch(self._GENERATE_TITLE_PATH) as mock_generate_title:
            subscriber = AutoTitleSubscriber(service=service)
            await subscriber(event)
            await asyncio.sleep(0)
            mock_generate_title.assert_not_called()

        assert service.stored.title is None

    @pytest.mark.asyncio
    async def test_autotitle_uses_llm_profile_when_configured(self):
        """Profile LLM takes precedence over agent.llm when configured."""
        service = self._make_service(title_llm_profile="cheap-model")
        mock_llm = LLM(model="gpt-3.5-turbo", usage_id="title-llm")

        with (
            patch(
                "openhands.agent_server.persistence.store.get_llm_profile_store"
            ) as MockStore,
            patch(
                self._GENERATE_TITLE_PATH, return_value="✨ Profile LLM Title"
            ) as mock_generate_title,
        ):
            mock_store_instance = MockStore.return_value
            mock_store_instance.load.return_value = mock_llm

            subscriber = AutoTitleSubscriber(service=service)
            await subscriber(self._user_message_event())
            await self._drain_title_task(lambda: service.stored.title is not None)

            MockStore.assert_called_once_with()
            mock_store_instance.load.assert_called_once_with(
                "cheap-model", cipher=service.cipher
            )
            # Profile-loaded LLM wins over agent.llm
            assert mock_generate_title.called
            assert mock_generate_title.call_args.args[1] is mock_llm

        assert service.stored.title == "✨ Profile LLM Title"
        service.save_meta.assert_called_once()

    @pytest.mark.asyncio
    async def test_autotitle_falls_back_to_agent_llm_when_profile_not_found(self):
        """Missing profile → fall back to agent.llm (non-breaking behavior)."""
        service = self._make_service(title_llm_profile="nonexistent-profile")
        agent_llm = service._conversation.agent.llm

        with (
            patch(
                "openhands.agent_server.persistence.store.get_llm_profile_store"
            ) as MockStore,
            patch(
                self._GENERATE_TITLE_PATH, return_value="✨ Agent LLM Title"
            ) as mock_generate_title,
        ):
            mock_store_instance = MockStore.return_value
            mock_store_instance.load.side_effect = FileNotFoundError(
                "Profile 'nonexistent-profile' not found"
            )

            subscriber = AutoTitleSubscriber(service=service)
            await subscriber(self._user_message_event())
            await self._drain_title_task(lambda: service.stored.title is not None)

            # Failed profile load → falls back to agent.llm
            assert mock_generate_title.called
            assert mock_generate_title.call_args.args[1] is agent_llm

        assert service.stored.title == "✨ Agent LLM Title"
        service.save_meta.assert_called_once()

    @pytest.mark.asyncio
    async def test_autotitle_no_profile_uses_agent_llm(self):
        """No profile configured → use agent.llm (preserves existing behavior)."""
        service = self._make_service(title_llm_profile=None)
        agent_llm = service._conversation.agent.llm

        with patch(
            self._GENERATE_TITLE_PATH, return_value="✨ Agent LLM Title"
        ) as mock_generate_title:
            subscriber = AutoTitleSubscriber(service=service)
            await subscriber(self._user_message_event())
            await self._drain_title_task(lambda: service.stored.title is not None)

            # No profile → agent.llm is used (backwards compatible)
            assert mock_generate_title.called
            assert mock_generate_title.call_args.args[1] is agent_llm

        assert service.stored.title == "✨ Agent LLM Title"
        service.save_meta.assert_called_once()

    @pytest.mark.asyncio
    async def test_autotitle_handles_profile_load_value_error(self):
        """Profile load ValueError → fall back to agent.llm."""
        service = self._make_service(title_llm_profile="corrupted-profile")
        agent_llm = service._conversation.agent.llm

        with (
            patch(
                "openhands.agent_server.persistence.store.get_llm_profile_store"
            ) as MockStore,
            patch(
                self._GENERATE_TITLE_PATH, return_value="✨ Agent LLM Title"
            ) as mock_generate_title,
        ):
            mock_store_instance = MockStore.return_value
            mock_store_instance.load.side_effect = ValueError("Invalid profile format")

            subscriber = AutoTitleSubscriber(service=service)
            await subscriber(self._user_message_event())
            await self._drain_title_task(lambda: service.stored.title is not None)

            assert mock_generate_title.called
            assert mock_generate_title.call_args.args[1] is agent_llm

        assert service.stored.title == "✨ Agent LLM Title"
        service.save_meta.assert_called_once()

    @pytest.mark.asyncio
    async def test_autotitle_falls_back_for_acp_managed_llm(self):
        """ACP-managed agents with no title profile → truncation fallback."""
        service = self._make_service(llm_usage_id="acp-managed")
        subscriber = AutoTitleSubscriber(service=service)

        await subscriber(self._user_message_event("Fix the login bug"))
        await self._drain_title_task(lambda: service.stored.title is not None)

        assert service.stored.title == "Fix the login bug"
        service.save_meta.assert_called_once()

    @pytest.mark.asyncio
    async def test_autotitle_integration_routes_through_profile_store(
        self, tmp_path, monkeypatch, request
    ):
        """End-to-end: profile on disk → LLMProfileStore.load → title LLM call.

        Exercises the real wiring from AutoTitleSubscriber through LLMProfileStore
        to LLM.completion. Only the network boundary (LLM.completion) is mocked,
        so this catches regressions in profile loading, LLM passthrough, and the
        agent-server → SDK integration — the unit tests above only exercise
        AutoTitleSubscriber in isolation.
        """
        from litellm.types.utils import (
            Choices,
            Message as LiteLLMMessage,
            ModelResponse,
            Usage,
        )

        from openhands.sdk.llm import LLMResponse, MetricsSnapshot
        from openhands.sdk.llm.llm_profile_store import LLMProfileStore

        # Persist a real LLM profile to disk with a distinctive usage_id so we
        # can tell the title LLM apart from the agent's LLM in the assertion.
        profile_dir = tmp_path / "profiles"
        title_llm_on_disk = LLM(
            usage_id="title-llm",
            model="claude-haiku-4-5",
            api_key=SecretStr("title-key"),
        )
        LLMProfileStore(base_dir=profile_dir).save(
            "title-fast", title_llm_on_disk, include_secrets=True
        )

        service = self._make_service(title_llm_profile="title-fast")

        calls: list[str] = []

        def fake_completion(self_llm, _messages, **_kwargs):
            calls.append(self_llm.usage_id)
            msg = LiteLLMMessage(content="✨ Generated", role="assistant")
            choice = Choices(finish_reason="stop", index=0, message=msg)
            raw = ModelResponse(
                id="resp-1",
                choices=[choice],
                created=0,
                model=self_llm.model,
                object="chat.completion",
                usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )
            return LLMResponse(
                message=Message.from_llm_chat_message(choice["message"]),
                metrics=MetricsSnapshot(
                    model_name=self_llm.model,
                    accumulated_cost=0.0,
                    max_budget_per_task=None,
                    accumulated_token_usage=None,
                ),
                raw_response=raw,
            )

        # Point the agent-server profile store singleton at our tmp dir via
        # OH_PERSISTENCE_DIR so the real _load_title_llm code path finds our
        # on-disk profile under `{tmp_path}/profiles`.
        from openhands.agent_server.persistence import reset_stores

        monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path))
        reset_stores()
        # Clear the singleton at teardown even if an assertion raises, so the
        # stale store (pointing at the soon-deleted tmp_path) can't leak.
        request.addfinalizer(reset_stores)

        with patch(
            "openhands.sdk.llm.llm.LLM.completion",
            autospec=True,
            side_effect=fake_completion,
        ):
            subscriber = AutoTitleSubscriber(service=service)
            await subscriber(self._user_message_event("Fix the login bug"))
            # Wait for the background executor task to complete. The production
            # code uses run_in_executor, so sleep(0) is not enough.
            for _ in range(50):
                await asyncio.sleep(0.02)
                if service.stored.title is not None:
                    break

        # The profile's LLM (usage_id="title-llm") was called — not agent.llm
        # (usage_id="test-llm"). This is the regression-sensitive assertion.
        assert calls == ["title-llm"], (
            f"Expected only the title profile LLM to be called, got: {calls}"
        )
        assert service.stored.title == "✨ Generated"
        service.save_meta.assert_called_once()

    @pytest.mark.asyncio
    async def test_autotitle_decrypts_cipher_encrypted_title_profile(
        self, tmp_path, monkeypatch, request
    ):
        """Regression for #3164: a cipher-encrypted title-LLM profile must be
        decrypted on load so the title LLM sees the plaintext API key, not
        Fernet ciphertext.
        """
        from litellm.types.utils import (
            Choices,
            Message as LiteLLMMessage,
            ModelResponse,
            Usage,
        )

        from openhands.sdk.llm import LLMResponse, MetricsSnapshot
        from openhands.sdk.llm.llm_profile_store import LLMProfileStore
        from openhands.sdk.utils.cipher import Cipher

        cipher = Cipher("title-cipher-test-key")

        profile_dir = tmp_path / "profiles"
        LLMProfileStore(base_dir=profile_dir).save(
            "title-encrypted",
            LLM(
                usage_id="title-llm",
                model="claude-haiku-4-5",
                api_key=SecretStr("plaintext-title-key"),
            ),
            include_secrets=True,
            cipher=cipher,
        )

        service = self._make_service(title_llm_profile="title-encrypted")
        # Inject the cipher; AutoTitleSubscriber reads it via service.cipher.
        service.cipher = cipher

        seen_keys: list[str] = []

        def fake_completion(self_llm, _messages, **_kwargs):
            seen_keys.append(
                self_llm.api_key.get_secret_value() if self_llm.api_key else ""
            )
            msg = LiteLLMMessage(content="✨ Generated", role="assistant")
            choice = Choices(finish_reason="stop", index=0, message=msg)
            raw = ModelResponse(
                id="resp-1",
                choices=[choice],
                created=0,
                model=self_llm.model,
                object="chat.completion",
                usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )
            return LLMResponse(
                message=Message.from_llm_chat_message(choice["message"]),
                metrics=MetricsSnapshot(
                    model_name=self_llm.model,
                    accumulated_cost=0.0,
                    max_budget_per_task=None,
                    accumulated_token_usage=None,
                ),
                raw_response=raw,
            )

        from openhands.agent_server.persistence import reset_stores

        monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path))
        reset_stores()
        # Clear the singleton at teardown even if an assertion raises, so the
        # stale store (pointing at the soon-deleted tmp_path) can't leak.
        request.addfinalizer(reset_stores)

        with patch(
            "openhands.sdk.llm.llm.LLM.completion",
            autospec=True,
            side_effect=fake_completion,
        ):
            subscriber = AutoTitleSubscriber(service=service)
            await subscriber(self._user_message_event("Fix the login bug"))
            for _ in range(50):
                await asyncio.sleep(0.02)
                if service.stored.title is not None:
                    break

        assert seen_keys == ["plaintext-title-key"], (
            f"Expected title LLM to receive decrypted key, got: {seen_keys}"
        )


class TestACPActivityHeartbeatWiring:
    """Tests for _setup_acp_activity_heartbeat in EventService."""

    def test_acp_agent_gets_on_activity_wired(self):
        """_setup_acp_activity_heartbeat should set _on_activity on ACPAgent."""
        from openhands.agent_server.event_service import EventService
        from openhands.agent_server.server_details_router import (
            update_last_execution_time,
        )

        service = AsyncMock(spec=EventService)
        # Call the real method
        agent = ACPAgent(acp_command=["echo", "test"])
        assert agent._on_activity is None

        EventService._setup_acp_activity_heartbeat(service, agent)

        assert agent._on_activity is update_last_execution_time

    def test_non_acp_agent_unchanged(self):
        """_setup_acp_activity_heartbeat is a no-op for non-ACP agents."""
        from openhands.agent_server.event_service import EventService

        service = AsyncMock(spec=EventService)
        agent = Agent(llm=LLM(model="test-model"))

        # Should not raise and should not set any attribute
        EventService._setup_acp_activity_heartbeat(service, agent)
        assert not hasattr(agent, "_on_activity")


def _branch_events(conversation) -> list:
    """Log events excluding async ``ConversationStateUpdateEvent`` artifacts.

    Those state-sync artifacts are appended to the log asynchronously
    (``EventService._emit_event_from_thread``), so their position in
    ``_state.events`` relative to the synchronous message appends is racy.
    Filtering them keeps positional indexing and length invariants deterministic
    for the fork/navigate assertions below.
    """
    return [
        e
        for e in conversation._state.events
        if not isinstance(e, ConversationStateUpdateEvent)
    ]


class TestConversationTreeForkAndNavigate:
    """Service-level coverage for fork-from-event lineage and navigation."""

    async def _start_with_events(self, svc, workspace_dir, texts):
        """Start a conversation and append ``texts`` as user messages (no run)."""
        from openhands.sdk.testing import TestLLM
        from tests.agent_server.stress.scripts import (
            start_conversation_with_test_llm,
        )

        parent_llm = TestLLM(
            usage_id="test-llm",
            model="openai/gpt-4o",
            api_key=SecretStr("unused"),
        )
        info = await start_conversation_with_test_llm(
            svc,
            parent_llm=parent_llm,
            workspace_dir=str(workspace_dir),
            usage_id="test-llm",
            initial_text=texts[0],
        )
        event_service = await svc.get_event_service(info.id)
        assert event_service is not None
        for text in texts[1:]:
            await event_service.send_message(
                Message(role="user", content=[TextContent(text=text)]),
                run=False,
            )
        events = _branch_events(event_service.get_conversation())
        return info, event_service, events

    @pytest.mark.asyncio
    async def test_fork_from_event_slices_branch_and_records_lineage(self, tmp_path):
        """fork(from_event_id) copies only the branch and stamps lineage."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        async with ConversationService(
            conversations_dir=tmp_path / "conversations"
        ) as svc:
            info, source_service, events = await self._start_with_events(
                svc, workspace_dir, ["first", "second", "third"]
            )
            branch_point = next(e for e in events if isinstance(e, MessageEvent))
            expected_branch_ids = [
                e.id
                for e in source_service.get_conversation()._state.events.path_to_root(
                    branch_point.id
                )
            ]

            fork_info = await svc.fork_conversation(
                info.id, from_event_id=branch_point.id
            )

            assert fork_info is not None
            assert fork_info.forked_from_conversation_id == info.id
            assert fork_info.forked_from_event_id == branch_point.id
            assert fork_info.leaf_event_id == branch_point.id
            # forked_from_conversation_id must JSON-serialize in the same (dashed)
            # shape as ``id`` so clients can correlate the two over the wire.
            dumped = fork_info.model_dump(mode="json")
            assert dumped["forked_from_conversation_id"] == str(info.id)

            fork_service = await svc.get_event_service(fork_info.id)
            assert fork_service is not None
            fork_events = _branch_events(fork_service.get_conversation())
            # Only path_to_root(branch_point) was copied — the active branch up
            # to and including the branch point, not the whole log.
            assert [e.id for e in fork_events] == expected_branch_ids
            assert len(fork_events) < len(events)

            # Source is untouched by the fork.
            src_events = _branch_events(source_service.get_conversation())
            assert len(src_events) == len(events)

    @pytest.mark.asyncio
    async def test_whole_conversation_fork_has_no_branch_point(self, tmp_path):
        """fork() without from_event_id copies everything; lineage event is None."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        async with ConversationService(
            conversations_dir=tmp_path / "conversations"
        ) as svc:
            info, _, events = await self._start_with_events(
                svc, workspace_dir, ["first", "second"]
            )

            fork_info = await svc.fork_conversation(info.id)

            assert fork_info is not None
            assert fork_info.forked_from_conversation_id == info.id
            assert fork_info.forked_from_event_id is None
            fork_service = await svc.get_event_service(fork_info.id)
            assert fork_service is not None
            fork_events = _branch_events(fork_service.get_conversation())
            assert len(fork_events) == len(events)

    @pytest.mark.asyncio
    async def test_fork_unknown_event_raises_without_leaking_dir(self, tmp_path):
        """fork(from_event_id) with an unknown id raises and leaves no orphan dir."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        conversations_dir = tmp_path / "conversations"
        async with ConversationService(conversations_dir=conversations_dir) as svc:
            info, _, _ = await self._start_with_events(svc, workspace_dir, ["first"])
            before = {p.name for p in conversations_dir.iterdir()}
            with pytest.raises(ValueError, match="from_event_id"):
                await svc.fork_conversation(info.id, from_event_id="evt-missing")
            # Validation must fail-fast before any fork dir is written to disk.
            assert {p.name for p in conversations_dir.iterdir()} == before

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "navigate_to_none", [False, True], ids=["to_event", "to_empty_tree"]
    )
    async def test_navigate_moves_head_in_place(self, tmp_path, navigate_to_none):
        """navigate moves HEAD (to an event or empty tree), pruning no events."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        async with ConversationService(
            conversations_dir=tmp_path / "conversations"
        ) as svc:
            info, event_service, events = await self._start_with_events(
                svc, workspace_dir, ["first", "second", "third"]
            )
            target = None if navigate_to_none else events[0].id

            nav_info = await svc.navigate_conversation(info.id, event_id=target)

            assert nav_info is not None
            assert nav_info.leaf_event_id == target
            # All branches stay on disk — nothing is pruned.
            assert len(_branch_events(event_service.get_conversation())) == len(events)

    @pytest.mark.asyncio
    async def test_navigate_unknown_event_raises(self, tmp_path):
        """navigate to an unknown event raises ValueError."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        async with ConversationService(
            conversations_dir=tmp_path / "conversations"
        ) as svc:
            info, _, _ = await self._start_with_events(svc, workspace_dir, ["first"])
            with pytest.raises(ValueError, match="event_id"):
                await svc.navigate_conversation(info.id, event_id="evt-missing")

    @pytest.mark.asyncio
    async def test_navigate_missing_conversation_returns_none(self, tmp_path):
        """navigate on an unknown conversation returns None (router maps to 404)."""
        async with ConversationService(
            conversations_dir=tmp_path / "conversations"
        ) as svc:
            assert await svc.navigate_conversation(uuid4(), event_id=None) is None

    @pytest.mark.asyncio
    async def test_lineage_and_navigated_head_persist_across_restart(self, tmp_path):
        """Fork lineage (meta.json) and a navigated HEAD (base_state.json) survive
        a server restart — navigate deliberately skips save_meta and relies on the
        conversation state's own autosave."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        conversations_dir = tmp_path / "conversations"

        async with ConversationService(conversations_dir=conversations_dir) as svc:
            info, _, events = await self._start_with_events(
                svc, workspace_dir, ["first", "second", "third"]
            )
            src_id = info.id
            branch_point = events[1].id
            target_leaf = events[0].id

            fork_info = await svc.fork_conversation(src_id, from_event_id=branch_point)
            assert fork_info is not None
            fork_id = fork_info.id
            await svc.navigate_conversation(src_id, event_id=target_leaf)

        # Fresh service over the same dir = a process restart.
        async with ConversationService(conversations_dir=conversations_dir) as svc2:
            reloaded_src = await svc2.get_conversation(src_id)
            reloaded_fork = await svc2.get_conversation(fork_id)

            assert reloaded_src is not None
            assert reloaded_src.leaf_event_id == target_leaf
            assert reloaded_fork is not None
            assert reloaded_fork.forked_from_conversation_id == src_id
            assert reloaded_fork.forked_from_event_id == branch_point


class TestConversationSearchScaling:
    """Status-filtered search/count must not full-scan conversation state.

    Regression coverage for #3142, where both called ``get_state()`` on every
    catalog entry to read one enum field per conversation.
    """

    @staticmethod
    def _seed(conversations_dir: Path, count: int, workspace_dir: Path) -> list[UUID]:
        """Write ``count`` idle conversations straight to disk."""
        conversations_dir.mkdir(parents=True, exist_ok=True)
        ids = []
        for i in range(count):
            conversation_id = uuid4()
            target = conversations_dir / conversation_id.hex
            target.mkdir()
            stored = StoredConversation(
                id=conversation_id,
                agent=Agent(llm=LLM(model="gpt-4o", usage_id=f"llm-{i}"), tools=[]),
                workspace=LocalWorkspace(working_dir=str(workspace_dir)),
                confirmation_policy=NeverConfirm(),
            )
            stored.created_at = datetime(2026, 1, 1, tzinfo=UTC).replace(microsecond=i)
            stored.updated_at = stored.created_at
            (target / "meta.json").write_text(stored.model_dump_json())
            state = ConversationState(
                id=conversation_id,
                agent=stored.agent,
                workspace=stored.workspace,
                persistence_dir=str(target),
            )
            (target / "base_state.json").write_text(state.model_dump_json())
            ids.append(conversation_id)
        return ids

    @pytest.mark.asyncio
    async def test_filtered_search_loads_state_only_for_the_page(self, tmp_path):
        """A filtered page loads full state for page items, not the whole catalog."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        conversations_dir = tmp_path / "conversations"
        self._seed(conversations_dir, 40, workspace_dir)

        async with ConversationService(conversations_dir=conversations_dir) as svc:
            assert len(svc._conversation_records) == 40
            real_load = svc._load_persisted_state_sync
            loaded: list[UUID] = []

            def _tracking_load(conversation_id: UUID):
                loaded.append(conversation_id)
                return real_load(conversation_id)

            with patch.object(
                svc, "_load_persisted_state_sync", side_effect=_tracking_load
            ):
                page = await svc.search_conversations(
                    limit=5, execution_status=ConversationExecutionStatus.IDLE
                )

            assert len(page.items) == 5
            assert page.next_page_id is not None
            # Before the fix: 40, one full state load per conversation.
            assert len(loaded) == 5, (
                f"expected 5 full state loads (the page), got {len(loaded)}"
            )

    @pytest.mark.asyncio
    async def test_filtered_count_loads_no_state(self, tmp_path):
        """Counting by status must not load any full conversation state."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        conversations_dir = tmp_path / "conversations"
        self._seed(conversations_dir, 25, workspace_dir)

        async with ConversationService(conversations_dir=conversations_dir) as svc:
            with patch.object(
                svc, "_load_persisted_state_sync", side_effect=AssertionError
            ):
                idle = await svc.count_conversations(
                    execution_status=ConversationExecutionStatus.IDLE
                )
                running = await svc.count_conversations(
                    execution_status=ConversationExecutionStatus.RUNNING
                )
                total = await svc.count_conversations()

            assert idle == 25
            assert running == 0
            assert total == 25

    @pytest.mark.asyncio
    async def test_status_index_picks_up_on_disk_changes(self, tmp_path):
        """The cached status is invalidated when base_state.json changes."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        conversations_dir = tmp_path / "conversations"
        ids = self._seed(conversations_dir, 3, workspace_dir)

        async with ConversationService(conversations_dir=conversations_dir) as svc:
            assert (
                await svc.count_conversations(
                    execution_status=ConversationExecutionStatus.FINISHED
                )
                == 0
            )

            # Simulate another process finishing one conversation.
            base_state = conversations_dir / ids[0].hex / "base_state.json"
            payload = json.loads(base_state.read_text())
            payload["execution_status"] = ConversationExecutionStatus.FINISHED.value
            # Change the size too, so the test does not rely on mtime
            # granularity.
            payload["max_iterations"] = 1234567
            base_state.write_text(json.dumps(payload))

            assert (
                await svc.count_conversations(
                    execution_status=ConversationExecutionStatus.FINISHED
                )
                == 1
            )
            page = await svc.search_conversations(
                execution_status=ConversationExecutionStatus.FINISHED
            )
            assert [item.id for item in page.items] == [ids[0]]
            assert (
                await svc.count_conversations(
                    execution_status=ConversationExecutionStatus.IDLE
                )
                == 2
            )

    @pytest.mark.asyncio
    async def test_live_conversation_status_is_read_from_memory(self, tmp_path):
        """A live conversation answers the filter from its in-memory state."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        conversations_dir = tmp_path / "conversations"
        self._seed(conversations_dir, 5, workspace_dir)

        request = StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="live-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir=str(workspace_dir)),
            confirmation_policy=NeverConfirm(),
        )
        async with ConversationService(conversations_dir=conversations_dir) as svc:
            live, _ = await svc.start_conversation(request)
            assert svc._event_services is not None
            event_service = svc._event_services[live.id]

            # Flip the live state without touching disk.
            state = await event_service.get_state()
            with state:
                state.execution_status = ConversationExecutionStatus.RUNNING

            assert (
                await svc.count_conversations(
                    execution_status=ConversationExecutionStatus.RUNNING
                )
                == 1
            )
            page = await svc.search_conversations(
                execution_status=ConversationExecutionStatus.RUNNING
            )
            assert [item.id for item in page.items] == [live.id]
            assert (
                await svc.count_conversations(
                    execution_status=ConversationExecutionStatus.IDLE
                )
                == 5
            )
