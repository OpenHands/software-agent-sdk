import asyncio
import errno
import json
import threading
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from openhands.sdk import Agent, Conversation
from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import AgentErrorEvent, ObservationEvent
from openhands.sdk.io import InMemoryFileStore, LocalFileStore
from openhands.sdk.io.local import atomic_write_text
from openhands.sdk.io.storage_safety import StorageSafetyConfig, StorageSafetyError
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool.builtins.think import ThinkExecutor


@pytest.fixture
def storage_conversation(tmp_path, monkeypatch):
    usage = SimpleNamespace(total=100_000_000, used=80_000_000, free=20_000_000)
    monkeypatch.setattr(
        "openhands.sdk.io.storage_safety.shutil.disk_usage", lambda _: usage
    )
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                tool_calls=[
                    MessageToolCall(
                        id="thought",
                        name="think",
                        arguments='{"thought":"retain this result"}',
                        origin="completion",
                    )
                ],
            ),
            Message(
                role="assistant",
                tool_calls=[
                    MessageToolCall(
                        id="finish",
                        name="finish",
                        arguments='{"message":"done"}',
                        origin="completion",
                    )
                ],
            ),
        ]
    )
    notifications: list[StorageSafetyError] = []
    conversation = Conversation(
        agent=Agent(llm=llm, tools=[], condenser=None),
        workspace=tmp_path / "workspace",
        persistence_dir=tmp_path / "history",
        storage_safety=StorageSafetyConfig(),
        storage_safety_callback=notifications.append,
        visualizer=None,
    )
    conversation.send_message("Remember the complete result")
    yield conversation, llm, usage, notifications
    conversation.close()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_low_space_preserves_inflight_result_and_requires_explicit_run(
    storage_conversation, monkeypatch, asynchronous
):
    conversation, llm, usage, notifications = storage_conversation
    execute = ThinkExecutor.__call__

    def consume_space(self, action, conversation=None):
        usage.free = 4_000_000
        return execute(self, action, conversation)

    monkeypatch.setattr(ThinkExecutor, "__call__", consume_space)
    with pytest.raises(StorageSafetyError) as caught:
        if asynchronous:
            asyncio.run(conversation.arun())
        else:
            conversation.run()
    assert caught.value.code == "StorageLowSpace"
    assert conversation.state.execution_status == ConversationExecutionStatus.PAUSED
    results = [e for e in conversation.state.events if isinstance(e, ObservationEvent)]
    assert len(results) == 1
    assert results[0].tool_call_id == "thought"
    assert llm.call_count == 1
    assert notifications[-1].code == "StorageLowSpace"
    with pytest.raises(StorageSafetyError):
        conversation.send_message("Start another task")
    usage.free = 20_000_000
    assert llm.call_count == 1
    conversation.run()
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED
    assert llm.call_count == 2


def test_watchdog_notifies_without_state_lock_or_cancelling_inflight_tool(
    storage_conversation, monkeypatch
):
    conversation, llm, usage, notifications = storage_conversation
    notified = threading.Event()
    monkeypatch.setattr(
        "openhands.sdk.conversation.impl.local_conversation._STORAGE_CHECK_INTERVAL",
        0.01,
    )
    conversation._storage_safety_callback = lambda error: (
        notifications.append(error),
        notified.set(),
    )
    execute = ThinkExecutor.__call__

    def wait_for_watchdog(self, action, conversation=None):
        usage.free = 4_000_000
        assert notified.wait(2), "watchdog must not wait for the state lock"
        return execute(self, action, conversation)

    monkeypatch.setattr(ThinkExecutor, "__call__", wait_for_watchdog)
    with pytest.raises(StorageSafetyError):
        asyncio.run(conversation.arun())
    assert llm.call_count == 1
    assert any(
        isinstance(event, ObservationEvent) for event in conversation.state.events
    )
    assert notifications[0].code == "StorageLowSpace"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_enospc_stops_once_and_recovery_never_reexecutes_unknown_tool(
    storage_conversation, monkeypatch, asynchronous
):
    conversation, llm, _, notifications = storage_conversation
    failed_writes: list[Path] = []
    executed: list[str] = []
    execute = ThinkExecutor.__call__

    def track_execution(self, action, conversation=None):
        executed.append(action.thought)
        return execute(self, action, conversation)

    def fail_observation(path: Path, value: str) -> None:
        if path.name.startswith("event-"):
            if json.loads(value)["kind"] == "ObservationEvent":
                failed_writes.append(path)
                raise OSError(errno.ENOSPC, "disk full")
        atomic_write_text(path, value)

    monkeypatch.setattr(ThinkExecutor, "__call__", track_execution)
    monkeypatch.setattr("openhands.sdk.io.local.atomic_write_text", fail_observation)
    with pytest.raises(StorageSafetyError) as caught:
        if asynchronous:
            asyncio.run(conversation.arun())
        else:
            conversation.run()
    assert caught.value.code == "StorageWriteFailed"
    assert caught.value.errno == errno.ENOSPC
    assert len(failed_writes) == 1
    assert executed == ["retain this result"]
    assert conversation.state.execution_status == ConversationExecutionStatus.ERROR
    assert notifications[-1].code == "StorageWriteFailed"
    with conversation.state:
        assert conversation.state.execution_status == ConversationExecutionStatus.ERROR
    assert len(failed_writes) == 1
    monkeypatch.setattr("openhands.sdk.io.local.atomic_write_text", atomic_write_text)
    with pytest.raises(StorageSafetyError) as recovery:
        conversation.run()
    assert recovery.value.code == "StorageRecoveryRequired"
    conversation.recover_storage(acknowledge_unknown_outcomes=True)
    assert any(
        isinstance(event, AgentErrorEvent) and event.tool_call_id == "thought"
        for event in conversation.state.events
    )
    conversation.run()
    assert executed == ["retain this result"]
    assert llm.call_count == 2
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED


def test_storage_safety_rejects_memory_store(tmp_path):
    agent = Agent(llm=TestLLM.from_messages([]), tools=[])
    with pytest.raises(ValueError, match="LocalFileStore"):
        LocalConversation(
            agent=agent,
            workspace=tmp_path,
            file_store=InMemoryFileStore(),
            storage_safety=StorageSafetyConfig(),
        )


def test_cold_recovery_reconciles_committed_action_with_stale_head(
    storage_conversation, monkeypatch
):
    conversation, llm, _, _ = storage_conversation
    assert conversation.state.persistence_dir is not None
    base_path = Path(conversation.state.persistence_dir) / "base_state.json"

    def fail_observation(path: Path, value: str) -> None:
        if path.name.startswith("event-"):
            if json.loads(value)["kind"] == "ObservationEvent":
                raise OSError(errno.ENOSPC, "disk full")
        atomic_write_text(path, value)

    monkeypatch.setattr("openhands.sdk.io.local.atomic_write_text", fail_observation)
    with pytest.raises(StorageSafetyError):
        conversation.run()
    stale_head = json.loads(base_path.read_text())["leaf_event_id"]
    assert stale_head != conversation.state.leaf_event_id
    committed_head = conversation.state.leaf_event_id
    assert committed_head is not None
    monkeypatch.setattr("openhands.sdk.io.local.atomic_write_text", atomic_write_text)
    with closing(
        LocalConversation(
            agent=conversation.agent,
            conversation_id=conversation.id,
            workspace=conversation.workspace,
            persistence_dir=base_path.parent.parent,
            storage_safety=StorageSafetyConfig(),
            visualizer=None,
        )
    ) as restored:
        assert restored.state.execution_status == ConversationExecutionStatus.ERROR
        with pytest.raises(StorageSafetyError) as caught:
            restored.run()
        assert caught.value.code == "StorageRecoveryRequired"
        with pytest.raises(ValueError, match="head_event_id"):
            restored.recover_storage(acknowledge_unknown_outcomes=True)
        with pytest.raises(ValueError, match="Unknown recovery"):
            restored.recover_storage(
                acknowledge_unknown_outcomes=True, head_event_id="missing-event"
            )
        with pytest.raises(StorageSafetyError):
            restored.run()
        assert llm.call_count == 1
        restored.recover_storage(
            acknowledge_unknown_outcomes=True, head_event_id=committed_head
        )
        assert any(
            isinstance(event, AgentErrorEvent) and event.tool_call_id == "thought"
            for event in restored.state.active_branch()
        )
        restored.run()
        assert restored.state.execution_status == ConversationExecutionStatus.FINISHED
    assert llm.call_count == 2


def test_workspace_and_history_filesystems_are_checked_independently(
    storage_conversation, monkeypatch
):
    conversation, llm, usage, _ = storage_conversation
    workspace = Path(conversation.workspace.working_dir)

    def disk_usage(path):
        if Path(path) == workspace:
            return SimpleNamespace(total=100_000_000, used=99_000_000, free=1_000_000)
        return usage

    monkeypatch.setattr("openhands.sdk.io.storage_safety.shutil.disk_usage", disk_usage)
    with pytest.raises(StorageSafetyError) as caught:
        conversation.run()
    assert caught.value.path == str(workspace)
    assert llm.call_count == 0


def test_write_failure_outside_run_requires_recovery(storage_conversation, monkeypatch):
    conversation, llm, _, _ = storage_conversation

    def fail_base_state(path: Path, value: str) -> None:
        if path.name == "base_state.json":
            raise OSError(errno.ENOSPC, "disk full")
        atomic_write_text(path, value)

    monkeypatch.setattr("openhands.sdk.io.local.atomic_write_text", fail_base_state)
    with pytest.raises(StorageSafetyError):
        conversation.send_message("This message reached the event log")
    monkeypatch.setattr("openhands.sdk.io.local.atomic_write_text", atomic_write_text)
    with pytest.raises(StorageSafetyError) as caught:
        conversation.run()
    assert caught.value.code == "StorageRecoveryRequired"
    assert llm.call_count == 0
    conversation.recover_storage(acknowledge_unknown_outcomes=True)
    conversation.run()
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED


def test_low_space_recovery_preserves_intentionally_selected_branch(
    storage_conversation,
):
    conversation, llm, usage, _ = storage_conversation
    chosen_head = conversation.state.leaf_event_id
    conversation.send_message("An abandoned future")
    abandoned_head = conversation.state.leaf_event_id
    conversation.navigate_to(chosen_head)
    usage.free = 4_000_000
    with pytest.raises(StorageSafetyError):
        conversation.check_storage_safety()
    usage.free = 20_000_000
    conversation.recover_storage()
    assert conversation.state.leaf_event_id == chosen_head
    assert abandoned_head in conversation.state.events
    assert abandoned_head not in {
        event.id for event in conversation.state.active_branch()
    }
    assert conversation.state.execution_status == ConversationExecutionStatus.IDLE
    assert llm.call_count == 0


@pytest.mark.parametrize("asynchronous", [False, True])
def test_mid_batch_write_failure_preserves_committed_results_without_reexecution(
    storage_conversation, monkeypatch, asynchronous
):
    conversation, _, _, _ = storage_conversation
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                tool_calls=[
                    MessageToolCall(
                        id=thought,
                        name="think",
                        arguments=json.dumps({"thought": thought}),
                        origin="completion",
                    )
                    for thought in ("first", "second")
                ],
            ),
            Message(role="assistant", content=[TextContent(text="done")]),
        ]
    )
    conversation.switch_llm(llm)
    executed: list[str] = []
    failed_writes: list[Path] = []
    execute = ThinkExecutor.__call__

    def track_execution(self, action, conversation=None):
        executed.append(action.thought)
        return execute(self, action, conversation)

    def fail_second_result(path: Path, value: str) -> None:
        if path.name.startswith("event-"):
            event = json.loads(value)
            if (
                event["kind"] == "ObservationEvent"
                and event["tool_call_id"] == "second"
            ):
                failed_writes.append(path)
                raise OSError(errno.ENOSPC, "disk full")
        atomic_write_text(path, value)

    monkeypatch.setattr(ThinkExecutor, "__call__", track_execution)
    monkeypatch.setattr("openhands.sdk.io.local.atomic_write_text", fail_second_result)
    with pytest.raises(StorageSafetyError):
        if asynchronous:
            asyncio.run(conversation.arun())
        else:
            conversation.run()
    assert sorted(executed) == ["first", "second"]
    assert len(failed_writes) == 1
    assert llm.call_count == 1
    assert conversation.state.persistence_dir is not None
    persisted = EventLog(LocalFileStore(conversation.state.persistence_dir))
    assert [
        event.tool_call_id for event in persisted if isinstance(event, ObservationEvent)
    ] == ["first"]
    monkeypatch.setattr("openhands.sdk.io.local.atomic_write_text", atomic_write_text)
    conversation.recover_storage(acknowledge_unknown_outcomes=True)
    assert [
        event.tool_call_id
        for event in conversation.state.active_branch()
        if isinstance(event, AgentErrorEvent)
    ] == ["second"]
    conversation.run()
    assert sorted(executed) == ["first", "second"]
    assert llm.call_count == 2
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED


@pytest.mark.parametrize("asynchronous", [False, True])
def test_snapshot_failure_after_result_commit_recovers_the_complete_result(
    storage_conversation, monkeypatch, asynchronous
):
    conversation, llm, _, _ = storage_conversation
    committed_results: list[str] = []
    failed_writes: list[Path] = []

    def fail_snapshot_after_result(path: Path, value: str) -> None:
        if path.name == "base_state.json" and committed_results:
            failed_writes.append(path)
            raise OSError(errno.ENOSPC, "disk full")
        atomic_write_text(path, value)
        if path.name.startswith("event-"):
            event = json.loads(value)
            if event["kind"] == "ObservationEvent":
                committed_results.append(event["id"])

    monkeypatch.setattr(
        "openhands.sdk.io.local.atomic_write_text", fail_snapshot_after_result
    )
    with pytest.raises(StorageSafetyError):
        if asynchronous:
            asyncio.run(conversation.arun())
        else:
            conversation.run()
    assert len(committed_results) == 1
    assert len(failed_writes) == 1
    assert llm.call_count == 1
    assert conversation.state.execution_status == ConversationExecutionStatus.ERROR
    assert conversation.state.persistence_dir is not None
    persistence_dir = Path(conversation.state.persistence_dir)
    snapshot = json.loads((persistence_dir / "base_state.json").read_text())
    assert snapshot["leaf_event_id"] != committed_results[0]
    persisted = EventLog(LocalFileStore(str(persistence_dir)))
    assert committed_results[0] in persisted
    monkeypatch.setattr("openhands.sdk.io.local.atomic_write_text", atomic_write_text)
    conversation.recover_storage(acknowledge_unknown_outcomes=True)
    assert not any(
        isinstance(event, AgentErrorEvent)
        for event in conversation.state.active_branch()
    )
    conversation.close()
    with closing(
        LocalConversation(
            agent=conversation.agent,
            conversation_id=conversation.id,
            workspace=conversation.workspace,
            persistence_dir=persistence_dir.parent,
            storage_safety=StorageSafetyConfig(),
            visualizer=None,
        )
    ) as restored:
        assert committed_results[0] in {
            event.id for event in restored.state.active_branch()
        }
        restored.run()
        assert restored.state.execution_status == ConversationExecutionStatus.FINISHED
    assert llm.call_count == 2
