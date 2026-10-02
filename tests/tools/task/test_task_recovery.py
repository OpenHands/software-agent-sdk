"""Durable task identities, interrupted children, and replay-free recovery."""

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, closing, contextmanager, nullcontext
from pathlib import Path
from uuid import uuid4

import pytest

from openhands.sdk import Agent, LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.io import LocalFileStore
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.subagent.registry import _reset_registry_for_tests, register_agent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool.execution_context import ToolInvocation
from openhands.tools.task.definition import TaskAction
from openhands.tools.task.manager import TaskManager, TaskStatus
from openhands.tools.task.recovery import (
    PersistedTask,
    TaskRevisionConflictError,
    TaskStore,
    recover_persisted_tasks,
)


def _agent() -> Agent:
    return Agent(
        llm=TestLLM.from_messages(
            [Message(role="assistant", content=[TextContent(text="finished work")])]
        ),
        tools=[],
    )


@pytest.fixture
def parent(tmp_path: Path) -> Iterator[LocalConversation]:
    _reset_registry_for_tests()
    register_agent(
        name="worker", factory_func=lambda llm: _agent(), description="Test worker"
    )
    with closing(
        LocalConversation(
            agent=_agent(),
            workspace=tmp_path,
            persistence_dir=tmp_path / "conversations",
            visualizer=None,
        )
    ) as conversation:
        yield conversation
    _reset_registry_for_tests()


def _store(parent: LocalConversation) -> TaskStore:
    assert parent.state.persistence_dir is not None
    return TaskStore(
        LocalFileStore(str(Path(parent.state.persistence_dir) / "subagents")),
        parent_conversation_id=parent.id,
        write_guard=nullcontext,
    )


def _reserve(parent: LocalConversation, call_id: str) -> PersistedTask:
    action = TaskAction(prompt="Work", subagent_type="worker")
    event = ActionEvent(
        thought=[],
        action=action,
        tool_name="task",
        tool_call_id=call_id,
        tool_call=MessageToolCall(
            id=call_id,
            name="task",
            arguments=action.model_dump_json(),
            origin="completion",
        ),
        llm_response_id="recovery-test-batch",
    )
    with parent.state:
        parent.state.append_event(event)
    return _store(parent).reserve(
        conversation_id=uuid4(),
        subagent_type="worker",
        invocation=ToolInvocation(action_id=event.id, tool_call_id=call_id),
    )


def _child(
    parent: LocalConversation, record: PersistedTask
) -> AbstractContextManager[LocalConversation]:
    assert parent.state.persistence_dir is not None
    return closing(
        LocalConversation(
            agent=_agent(),
            workspace=parent.state.workspace.working_dir,
            persistence_dir=Path(parent.state.persistence_dir) / "subagents",
            conversation_id=record.conversation_id,
            visualizer=None,
        )
    )


def _recover(parent: LocalConversation):
    with parent.state:
        return recover_persisted_tasks(
            parent.state, _store(parent), write_guard=nullcontext
        )


def test_completed_result_survives_crash_before_publication(
    parent: LocalConversation,
) -> None:
    record = _reserve(parent, "completed")
    with _child(parent, record) as child:
        child.send_message("Work")
        child.run()
        assert child.state.execution_status == ConversationExecutionStatus.FINISHED
    first = _recover(parent)
    assert first.records[0].status == "completed"
    assert first.observations[0].observation.text == "finished work"
    # Index reconciliation may finish before the parent observation is durable.
    retry = _recover(parent)
    assert len(retry.observations) == 1
    with parent.state:
        parent._on_event(retry.observations[0])
    assert _recover(parent).observations == []


def test_parallel_recovery_preserves_both_results_on_active_branch(
    parent: LocalConversation,
) -> None:
    records = [_reserve(parent, call_id) for call_id in ("first", "second")]
    for record in records:
        with _child(parent, record) as child:
            with child.state:
                child.state.execution_status = ConversationExecutionStatus.RUNNING
    # A crash can leave HEAD behind the already-written action files.
    with parent.state:
        assert records[0].invocation is not None
        parent.state.leaf_event_id = records[0].invocation.action_id
    recovered = _recover(parent)
    assert {ob.tool_call_id for ob in recovered.observations} == {"first", "second"}
    assert all(record.status == "error" for record in recovered.records)
    with parent.state:
        for observation in recovered.observations:
            parent._on_event(observation)
    branch = parent.state.active_branch()
    assert len([event for event in branch if isinstance(event, ActionEvent)]) == 2
    assert len([event for event in branch if isinstance(event, ObservationEvent)]) == 2
    assert _recover(parent).observations == []
    for record in records:
        path = (
            Path(parent.state.persistence_dir or "")
            / "subagents"
            / record.conversation_id.hex
        )
        assert (
            json.loads((path / "base_state.json").read_text())["execution_status"]
            == "error"
        )


def test_reserved_but_uninitialized_child_is_interrupted(
    parent: LocalConversation,
) -> None:
    _reserve(parent, "not-started")
    recovered = _recover(parent)
    assert recovered.records[0].status == "error"
    assert "not persisted" in recovered.observations[0].observation.text


def test_previous_completion_cannot_satisfy_a_resumed_invocation(
    parent: LocalConversation,
) -> None:
    record = _reserve(parent, "resume")
    with _child(parent, record) as child:
        child.send_message("Previous work")
        child.run()
        watermark = len(child.state.events)
    store = _store(parent)
    store.save(
        record.model_copy(update={"start_event_count": watermark}), expected_revision=0
    )
    recovered = _recover(parent)
    assert recovered.records[0].status == "error"
    assert recovered.records[0].result is None


def test_separate_stores_allocate_unique_ids_without_stale_cache(
    tmp_path: Path,
) -> None:
    parent_id = uuid4()

    def reserve(_: int) -> str:
        store = TaskStore(
            LocalFileStore(str(tmp_path)),
            parent_conversation_id=parent_id,
            write_guard=nullcontext,
        )
        return store.reserve(conversation_id=uuid4(), subagent_type="worker").task_id

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(reserve, range(12)))
    assert len(set(ids)) == 12
    assert reserve(12) not in ids


def test_stale_revision_cannot_overwrite_completed_task(
    parent: LocalConversation,
) -> None:
    first, second = _store(parent), _store(parent)
    record = first.reserve(conversation_id=uuid4(), subagent_type="worker")
    assert second.load_all() == [record]
    first.save(
        record.model_copy(update={"status": "completed", "result": "done"}),
        expected_revision=0,
    )
    with pytest.raises(TaskRevisionConflictError):
        second.save(record.model_copy(update={"status": "error"}), expected_revision=0)
    assert second.load_all()[0].result == "done"


@pytest.mark.parametrize("contents", ["{", '{"schema_version": 99}'])
def test_corrupt_or_future_index_is_not_silently_reset(
    parent: LocalConversation, contents: str
) -> None:
    index = Path(parent.state.persistence_dir or "") / "subagents" / "tasks.json"
    index.parent.mkdir(exist_ok=True)
    index.write_text(contents)
    with pytest.raises(ValueError):
        _store(parent).load_all()
    assert index.read_text() == contents


def test_lost_ownership_fences_task_index_writes(tmp_path: Path) -> None:
    owned = True

    @contextmanager
    def guard() -> Iterator[None]:
        if not owned:
            raise PermissionError("ownership lost")
        yield

    store = TaskStore(
        LocalFileStore(str(tmp_path)), parent_conversation_id=uuid4(), write_guard=guard
    )
    record = store.reserve(conversation_id=uuid4(), subagent_type="worker")
    owned = False
    with pytest.raises(PermissionError, match="ownership lost"):
        store.save(
            record.model_copy(update={"status": "completed"}), expected_revision=0
        )
    assert store.load_all() == [record]


def test_legacy_child_is_interrupted_without_inventing_parent_result(
    parent: LocalConversation,
) -> None:
    orphan = PersistedTask(
        task_id="legacy",
        parent_conversation_id=parent.id,
        conversation_id=uuid4(),
        subagent_type="worker",
        status="running",
    )
    with _child(parent, orphan) as child:
        with child.state:
            child.state.execution_status = ConversationExecutionStatus.RUNNING
    assert _recover(parent).observations == []
    store = _store(parent)
    ids = [
        store.reserve(conversation_id=uuid4(), subagent_type="worker").task_id
        for _ in range(2)
    ]
    assert all(len(task_id) > len("task_00000001") for task_id in ids)


def test_nested_children_are_reconciled(parent: LocalConversation) -> None:
    outer = _reserve(parent, "outer")
    with _child(parent, outer) as child:
        inner = _reserve(child, "inner")
        with _child(child, inner) as grandchild:
            with grandchild.state:
                grandchild.state.execution_status = ConversationExecutionStatus.RUNNING
        with child.state:
            child.state.execution_status = ConversationExecutionStatus.RUNNING
    assert _recover(parent).records[0].status == "error"
    assert parent.state.persistence_dir is not None
    child_path = (
        Path(parent.state.persistence_dir) / "subagents" / outer.conversation_id.hex
    )
    nested = json.loads((child_path / "subagents" / "tasks.json").read_text())
    assert nested["tasks"][inner.task_id]["status"] == "error"


def test_manager_restores_and_resumes_task_and_avoids_id_reuse(
    parent: LocalConversation,
) -> None:
    first = TaskManager()
    task = first.start_task("Work", "worker", conversation=parent)
    assert task.status == TaskStatus.COMPLETED
    first.close()
    restored = TaskManager(parent_state=parent.state)
    try:
        continued = restored.start_task(
            "Continue", "worker", resume=task.id, conversation=parent
        )
        assert continued.status == TaskStatus.COMPLETED
        assert continued.conversation_id == task.conversation_id
        new_task = restored.start_task("New work", "worker")
        assert new_task.id != task.id
    finally:
        restored.close()


def test_another_manager_cannot_resume_a_live_task(parent: LocalConversation) -> None:
    owner = TaskManager()
    owner.attach_parent(parent)
    task = owner._create_task("worker", None)
    other = TaskManager(parent_state=parent.state)
    other.attach_parent(parent)
    try:
        with pytest.raises(ValueError, match="running|live"):
            other.start_task("Duplicate", "worker", resume=task.id)
    finally:
        owner._evict_task(task)
        owner.close()
        other.close()


def test_close_before_parent_attachment_keeps_persisted_tasks(
    parent: LocalConversation,
) -> None:
    record = _reserve(parent, "unattached")
    restored = TaskManager(parent_state=parent.state)
    assert restored._tasks[record.task_id].conversation_id == record.conversation_id
    restored.close()
    assert _store(parent).load_all() == [record]
