"""Durable task identity and recovery without restarting child execution."""

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.conversation.persistence_const import BASE_STATE
from openhands.sdk.conversation.response_utils import get_agent_final_response
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event import ActionEvent, Event, MessageEvent, ObservationBaseEvent
from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.event.llm_convertible.observation import ObservationEvent
from openhands.sdk.io import FileStore, LocalFileStore
from openhands.sdk.tool.builtins import FinishAction
from openhands.sdk.tool.execution_context import ToolInvocation
from openhands.tools.task.definition import TaskAction, TaskObservation


_INDEX_PATH = "tasks.json"
_INDEX_LOCK = ".tasks.lock"
_INTERRUPTED = "Sub-agent execution was interrupted by a process restart."


class PersistedTask(BaseModel):
    """Durable task metadata without live runtime resources."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    task_id: str = Field(description="Stable task ID exposed to the parent.")
    parent_conversation_id: UUID = Field(description="Owning parent conversation.")
    conversation_id: UUID = Field(description="Persisted child conversation.")
    subagent_type: str = Field(description="Registered sub-agent type.")
    invocation: ToolInvocation | None = Field(
        default=None,
        description="Parent tool call, absent for direct programmatic delegation.",
    )
    status: Literal["running", "completed", "error"] = Field(
        description="Persisted task lifecycle state."
    )
    revision: int = Field(default=0, ge=0, description="Optimistic write revision.")
    start_event_count: int = Field(
        default=0, ge=0, description="Child event count before this task invocation."
    )
    result: str | None = Field(default=None, description="Successful task result.")
    error: str | None = Field(default=None, description="Failure or interruption.")


class TaskRevisionConflictError(RuntimeError):
    """A task was updated after the caller read its persisted revision."""


class _TaskIndex(BaseModel):
    schema_version: Literal[1] = 1
    next_id: int = Field(default=1, ge=1)
    legacy_ids: bool = False
    tasks: dict[str, PersistedTask] = Field(default_factory=dict)


class TaskStore:
    """A parent-scoped task index using the existing file-store primitives."""

    def __init__(
        self,
        file_store: FileStore,
        *,
        parent_conversation_id: UUID,
        write_guard: Callable[[], AbstractContextManager[None]],
    ) -> None:
        """Bind an index to its parent and the parent's ownership write guard."""
        # Several managers may share one parent. LocalFileStore's normal cache
        # assumes an exclusive writer, which a read/modify/write index cannot.
        self._fs = (
            LocalFileStore(file_store.root, cache_memory_size=0)
            if isinstance(file_store, LocalFileStore)
            else file_store
        )
        self._parent_id = parent_conversation_id
        self._write_guard = write_guard

    def _read(self) -> _TaskIndex:
        try:
            index = _TaskIndex.model_validate_json(self._fs.read(_INDEX_PATH))
        except FileNotFoundError:
            return _TaskIndex()
        for task_id, record in index.tasks.items():
            if (
                task_id != record.task_id
                or record.parent_conversation_id != self._parent_id
            ):
                raise ValueError("Task index contains an inconsistent task identity")
        return index

    def reserve(
        self,
        *,
        conversation_id: UUID,
        subagent_type: str,
        invocation: ToolInvocation | None = None,
    ) -> PersistedTask:
        """Atomically reserve an ID and record before starting child execution."""
        with self._fs.lock(_INDEX_LOCK):
            index = self._read()
            task_id = f"task_{index.next_id:08x}"
            # Pre-index conversations have no reliable numeric high-water mark.
            # Use a disjoint ID shape rather than colliding with a legacy task.
            if not index.tasks and any(p.endswith("/") for p in self._fs.list("")):
                index.legacy_ids = True
            if index.legacy_ids:
                task_id = f"task_{uuid4().hex}"
            while task_id in index.tasks:
                index.next_id += 1
                task_id = f"task_{index.next_id:08x}"
            record = PersistedTask(
                task_id=task_id,
                parent_conversation_id=self._parent_id,
                conversation_id=conversation_id,
                subagent_type=subagent_type,
                invocation=invocation,
                status="running",
            )
            index.tasks[task_id] = record
            index.next_id += 1
            with self._write_guard():
                self._fs.write(_INDEX_PATH, index.model_dump_json())
            return record

    def load_all(self) -> list[PersistedTask]:
        """Read records without treating corrupt or unsupported data as empty."""
        with self._fs.lock(_INDEX_LOCK):
            return list(self._read().tasks.values())

    def save(self, record: PersistedTask, *, expected_revision: int) -> PersistedTask:
        """Save a matching revision atomically and return its incremented version.

        Raises:
            TaskRevisionConflictError: The stored revision no longer matches.
        """
        with self._fs.lock(_INDEX_LOCK):
            index = self._read()
            current = index.tasks.get(record.task_id)
            if current is None or current.revision != expected_revision:
                raise TaskRevisionConflictError("Task revision no longer matches")
            if (
                record.parent_conversation_id != self._parent_id
                or record.conversation_id != current.conversation_id
            ):
                raise ValueError("Cannot change a persisted task's conversation")
            updated = record.model_copy(update={"revision": expected_revision + 1})
            index.tasks[record.task_id] = updated
            with self._write_guard():
                self._fs.write(_INDEX_PATH, index.model_dump_json())
            return updated


class TaskRecoveryResult(BaseModel):
    """Reconciled task records and parent observations awaiting publication."""

    records: list[PersistedTask] = Field(default_factory=list)
    observations: list[ObservationEvent] = Field(default_factory=list)


def recover_persisted_tasks(
    parent_state: ConversationState,
    task_store: TaskStore,
    *,
    write_guard: Callable[[], AbstractContextManager[None]],
) -> TaskRecoveryResult:
    """Reconcile children after exclusive parent ownership has been acquired.

    The write guard must fence child-state writes as well as index updates.
    Recovery must not initialize tools, connect MCP servers, or replay actions.
    Existing terminal child results are preserved; abandoned running children
    receive an explicit interruption result.

    Returned observations are linked to their parent action and exclude calls
    already observed in the durable parent log. The caller publishes them through
    the existing conversation event callback under its ownership guard. A later
    recovery must retry publication after a crash between reconciliation and
    publication, without duplicating a previously published observation.
    """
    if parent_state.persistence_dir is None:
        return TaskRecoveryResult()
    return _recover_directory(
        Path(parent_state.persistence_dir),
        list(parent_state.events),
        task_store,
        write_guard,
    )


def _recover_directory(
    parent_dir: Path,
    events: list[Event],
    task_store: TaskStore,
    write_guard: Callable[[], AbstractContextManager[None]],
) -> TaskRecoveryResult:
    subagents_dir = parent_dir / "subagents"
    records = []
    known_children: set[UUID] = set()
    for record in task_store.load_all():
        known_children.add(record.conversation_id)
        status, result, error = _recover_child(
            subagents_dir / record.conversation_id.hex,
            record.start_event_count if record.status == "running" else 0,
            record.status == "running",
            write_guard,
        )
        if record.status == "running":
            record = task_store.save(
                record.model_copy(
                    update={"status": status, "result": result, "error": error}
                ),
                expected_revision=record.revision,
            )
        records.append(record)

    # Old children can be terminalized without inventing a task/tool-call link.
    if subagents_dir.exists():
        for child_dir in sorted(subagents_dir.iterdir()):
            if not child_dir.is_dir():
                continue
            try:
                child_id = UUID(hex=child_dir.name)
            except ValueError:
                continue
            if child_id not in known_children:
                _recover_child(child_dir, 0, False, write_guard)

    return TaskRecoveryResult(
        records=records, observations=_pending_observations(records, events)
    )


def _recover_child(
    child_dir: Path,
    start_event_count: int,
    pending: bool,
    write_guard: Callable[[], AbstractContextManager[None]],
) -> tuple[Literal["completed", "error"], str | None, str | None]:
    if child_dir.is_symlink():
        raise ValueError("Refusing to recover a symlinked child conversation")
    if not (child_dir / BASE_STATE).exists():
        return "error", None, f"{_INTERRUPTED} Child state was not persisted."
    fs = LocalFileStore(str(child_dir), cache_memory_size=0)
    # Update only lifecycle/HEAD fields: reserializing an Agent without its
    # original cipher could discard persisted encrypted credentials.
    base = json.loads(fs.read(BASE_STATE))
    child_id = UUID(base["id"])
    if child_id.hex != child_dir.name:
        raise ValueError("Child conversation ID does not match its directory")
    events = EventLog(fs)
    invocation_events = list(events)[start_event_count:]
    if (child_dir / "subagents").exists():
        nested = _recover_directory(
            child_dir,
            list(events),
            TaskStore(
                LocalFileStore(str(child_dir / "subagents")),
                parent_conversation_id=child_id,
                write_guard=write_guard,
            ),
            write_guard,
        )
        for observation in nested.observations:
            with write_guard():
                events.append(observation)
                base["leaf_event_id"] = observation.id
                base["head_is_empty"] = False
                fs.write(BASE_STATE, json.dumps(base))

    has_final_response = any(
        (isinstance(event, MessageEvent) and event.source == "agent")
        or (isinstance(event, ActionEvent) and isinstance(event.action, FinishAction))
        for event in invocation_events
    )
    if base["execution_status"] == "finished" and has_final_response:
        return "completed", get_agent_final_response(invocation_events), None

    if base["execution_status"] == "error":
        errors = [
            event
            for event in invocation_events
            if isinstance(event, ConversationErrorEvent)
        ]
        if errors:
            return "error", None, errors[-1].detail

    if pending or base["execution_status"] == "running":
        previous_errors = [
            event
            for event in invocation_events
            if isinstance(event, ConversationErrorEvent)
            and event.code == "task_interrupted"
        ]
        with write_guard():
            if not previous_errors:
                events.append(
                    ConversationErrorEvent(
                        source="environment",
                        code="task_interrupted",
                        detail=_INTERRUPTED,
                    )
                )
            base["execution_status"] = "error"
            fs.write(BASE_STATE, json.dumps(base))
    return "error", None, _INTERRUPTED


def _pending_observations(
    records: list[PersistedTask], events: list[Event]
) -> list[ObservationEvent]:
    by_action = {
        record.invocation.action_id: record
        for record in records
        if record.invocation is not None
    }
    observed = {
        event.tool_call_id
        for event in events
        if isinstance(event, ObservationBaseEvent)
    }
    # Anchor the whole recovered batch to its durable tail, then chain results.
    # Anchoring every result to its own action would fork concurrent calls.
    parent_id = next(
        (
            event.id
            for event in reversed(events)
            if isinstance(event, LLMConvertibleEvent)
        ),
        None,
    )
    observations = []
    for event in events:
        if not isinstance(event, ActionEvent) or not isinstance(
            event.action, TaskAction
        ):
            continue
        record = by_action.get(event.id)
        if record is None or event.tool_call_id in observed:
            continue
        assert record.invocation is not None
        if record.invocation.tool_call_id != event.tool_call_id:
            raise ValueError("Persisted task tool call does not match its action")
        observation = ObservationEvent(
            parent_id=parent_id,
            action_id=event.id,
            tool_call_id=event.tool_call_id,
            tool_name=event.tool_name,
            observation=TaskObservation.from_text(
                text=record.result or record.error or "Task completed with no result.",
                task_id=record.task_id,
                subagent=record.subagent_type,
                status=record.status,
                is_error=record.status == "error",
            ),
        )
        observations.append(observation)
        observed.add(event.tool_call_id)
        parent_id = observation.id
    return observations
