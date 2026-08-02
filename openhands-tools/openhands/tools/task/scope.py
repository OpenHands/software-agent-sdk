"""Deterministic preflight checks for parallel sub-agent task scopes.

Scope paths are workspace-relative POSIX paths. A trailing ``/**`` declares a
recursive directory scope; all other paths are exact. Other glob syntax is
rejected so overlap checks remain deterministic and side-effect free.
"""

from collections.abc import Sequence
from enum import StrEnum
from itertools import combinations
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskScopeDecision(StrEnum):
    """Scheduling guidance produced by :func:`analyze_task_scopes`."""

    ALLOW_PARALLEL = "allow_parallel"
    SERIALIZE = "serialize"
    REJECT_CONFLICT = "reject_conflict"


class TaskScopeConflictKind(StrEnum):
    """Kinds of conflicts found during task-scope analysis."""

    DEPENDENCY = "dependency"
    DEPENDENCY_CYCLE = "dependency_cycle"
    READ_WRITE = "read_write"
    SELF_DEPENDENCY = "self_dependency"
    UNKNOWN_DEPENDENCY = "unknown_dependency"
    WRITE_WRITE = "write_write"


class TaskScope(BaseModel):
    """Declared workspace access and dependencies for one planned task."""

    model_config = ConfigDict(frozen=True)

    task_id: str = Field(min_length=1, description="Stable identifier for the task.")
    read_paths: tuple[str, ...] = Field(
        default=(), description="Workspace-relative paths the task may read."
    )
    write_paths: tuple[str, ...] = Field(
        default=(), description="Workspace-relative paths the task may modify."
    )
    depends_on: tuple[str, ...] = Field(
        default=(), description="Task IDs that must complete before this task."
    )

    @field_validator("task_id")
    @classmethod
    def _validate_task_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("task_id must not be empty")
        return value

    @field_validator("read_paths", "write_paths")
    @classmethod
    def _normalize_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(_normalize_scope_path(value) for value in values))

    @field_validator("depends_on")
    @classmethod
    def _normalize_dependencies(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = (value.strip() for value in values)
        return tuple(dict.fromkeys(value for value in normalized if value))


class TaskScopeConflict(BaseModel):
    """One reason a set of tasks cannot safely run in parallel."""

    model_config = ConfigDict(frozen=True)

    kind: TaskScopeConflictKind
    task_ids: tuple[str, ...]
    path_pairs: tuple[tuple[str, str], ...] = ()


class TaskScopeAnalysis(BaseModel):
    """Deterministic scheduling guidance and the evidence behind it."""

    model_config = ConfigDict(frozen=True)

    decision: TaskScopeDecision
    conflicts: tuple[TaskScopeConflict, ...] = ()


_REJECTING_CONFLICTS = {
    TaskScopeConflictKind.DEPENDENCY_CYCLE,
    TaskScopeConflictKind.SELF_DEPENDENCY,
    TaskScopeConflictKind.UNKNOWN_DEPENDENCY,
    TaskScopeConflictKind.WRITE_WRITE,
}


def analyze_task_scopes(scopes: Sequence[TaskScope]) -> TaskScopeAnalysis:
    """Analyze task scopes before scheduling parallel sub-agents.

    Write/write overlap is rejected because two tasks claim ownership of the
    same output. Read/write overlap and explicit dependencies can be made safe
    by serialization. Invalid dependency references and dependency cycles are
    rejected.
    """

    ordered = tuple(sorted(scopes, key=lambda scope: scope.task_id))
    by_id = {scope.task_id: scope for scope in ordered}
    if len(by_id) != len(ordered):
        raise ValueError("task_id values must be unique")

    conflicts: list[TaskScopeConflict] = []
    conflicts.extend(_dependency_conflicts(ordered, by_id))
    conflicts.extend(_path_conflicts(ordered))
    conflicts.sort(key=_conflict_sort_key)

    if any(conflict.kind in _REJECTING_CONFLICTS for conflict in conflicts):
        decision = TaskScopeDecision.REJECT_CONFLICT
    elif conflicts:
        decision = TaskScopeDecision.SERIALIZE
    else:
        decision = TaskScopeDecision.ALLOW_PARALLEL

    return TaskScopeAnalysis(decision=decision, conflicts=tuple(conflicts))


def _dependency_conflicts(
    scopes: tuple[TaskScope, ...], by_id: dict[str, TaskScope]
) -> list[TaskScopeConflict]:
    conflicts: list[TaskScopeConflict] = []
    graph: dict[str, tuple[str, ...]] = {}

    for scope in scopes:
        valid_dependencies: list[str] = []
        for dependency in sorted(scope.depends_on):
            if dependency == scope.task_id:
                conflicts.append(
                    TaskScopeConflict(
                        kind=TaskScopeConflictKind.SELF_DEPENDENCY,
                        task_ids=(scope.task_id,),
                    )
                )
            elif dependency not in by_id:
                conflicts.append(
                    TaskScopeConflict(
                        kind=TaskScopeConflictKind.UNKNOWN_DEPENDENCY,
                        task_ids=(scope.task_id, dependency),
                    )
                )
            else:
                valid_dependencies.append(dependency)
                conflicts.append(
                    TaskScopeConflict(
                        kind=TaskScopeConflictKind.DEPENDENCY,
                        task_ids=(scope.task_id, dependency),
                    )
                )
        graph[scope.task_id] = tuple(valid_dependencies)

    for cycle in _find_dependency_cycles(graph):
        conflicts.append(
            TaskScopeConflict(
                kind=TaskScopeConflictKind.DEPENDENCY_CYCLE,
                task_ids=cycle,
            )
        )

    return conflicts


def _path_conflicts(scopes: tuple[TaskScope, ...]) -> list[TaskScopeConflict]:
    conflicts: list[TaskScopeConflict] = []

    for left, right in combinations(scopes, 2):
        write_write = _overlapping_pairs(left.write_paths, right.write_paths)
        if write_write:
            conflicts.append(
                TaskScopeConflict(
                    kind=TaskScopeConflictKind.WRITE_WRITE,
                    task_ids=(left.task_id, right.task_id),
                    path_pairs=write_write,
                )
            )

        left_reads_right_writes = _overlapping_pairs(
            left.read_paths, right.write_paths
        )
        if left_reads_right_writes:
            conflicts.append(
                TaskScopeConflict(
                    kind=TaskScopeConflictKind.READ_WRITE,
                    task_ids=(left.task_id, right.task_id),
                    path_pairs=left_reads_right_writes,
                )
            )

        right_reads_left_writes = _overlapping_pairs(
            right.read_paths, left.write_paths
        )
        if right_reads_left_writes:
            conflicts.append(
                TaskScopeConflict(
                    kind=TaskScopeConflictKind.READ_WRITE,
                    task_ids=(right.task_id, left.task_id),
                    path_pairs=right_reads_left_writes,
                )
            )

    return conflicts


def _normalize_scope_path(value: str) -> str:
    raw = value.strip().replace("\\", "/")
    if not raw:
        raise ValueError("scope paths must not be empty")

    if raw == "**":
        return raw

    recursive = raw.endswith("/**")
    base = raw[:-3] if recursive else raw
    if any(character in base for character in "*?[]"):
        raise ValueError("only a trailing '/**' recursive scope is supported")

    path = PurePosixPath(base)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("scope paths must stay within the workspace")

    normalized = path.as_posix()
    if normalized in {"", "."}:
        raise ValueError("use '**' to declare a workspace-wide scope")

    return f"{normalized}/**" if recursive else normalized


def _overlapping_pairs(
    left_paths: tuple[str, ...], right_paths: tuple[str, ...]
) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            {
                (left, right)
                for left in left_paths
                for right in right_paths
                if _paths_overlap(left, right)
            }
        )
    )


def _paths_overlap(left: str, right: str) -> bool:
    left_base, left_recursive = _split_scope_path(left)
    right_base, right_recursive = _split_scope_path(right)

    if left_recursive and right_recursive:
        return _is_within(left_base, right_base) or _is_within(
            right_base, left_base
        )
    if left_recursive:
        return _is_within(right_base, left_base)
    if right_recursive:
        return _is_within(left_base, right_base)
    return left_base == right_base


def _split_scope_path(path: str) -> tuple[str, bool]:
    if path == "**":
        return "", True
    if path.endswith("/**"):
        return path[:-3], True
    return path, False


def _is_within(path: str, directory: str) -> bool:
    return not directory or path == directory or path.startswith(f"{directory}/")


def _find_dependency_cycles(
    graph: dict[str, tuple[str, ...]],
) -> tuple[tuple[str, ...], ...]:
    state: dict[str, int] = {}
    stack: list[str] = []
    stack_positions: dict[str, int] = {}
    cycles: set[tuple[str, ...]] = set()

    def visit(task_id: str) -> None:
        state[task_id] = 1
        stack_positions[task_id] = len(stack)
        stack.append(task_id)

        for dependency in graph[task_id]:
            dependency_state = state.get(dependency, 0)
            if dependency_state == 0:
                visit(dependency)
            elif dependency_state == 1:
                start = stack_positions[dependency]
                cycles.add(_canonical_cycle(tuple(stack[start:])))

        stack.pop()
        stack_positions.pop(task_id)
        state[task_id] = 2

    for task_id in sorted(graph):
        if state.get(task_id, 0) == 0:
            visit(task_id)

    return tuple(sorted(cycles))


def _canonical_cycle(cycle: tuple[str, ...]) -> tuple[str, ...]:
    rotations = tuple(cycle[index:] + cycle[:index] for index in range(len(cycle)))
    return min(rotations)


def _conflict_sort_key(conflict: TaskScopeConflict) -> tuple:
    return (conflict.kind.value, conflict.task_ids, conflict.path_pairs)
