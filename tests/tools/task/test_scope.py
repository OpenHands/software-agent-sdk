import pytest
from pydantic import ValidationError

from openhands.tools.task.scope import (
    TaskScope,
    TaskScopeConflictKind,
    TaskScopeDecision,
    analyze_task_scopes,
)


def test_independent_scopes_allow_parallel_execution():
    analysis = analyze_task_scopes(
        [
            TaskScope(task_id="auth", write_paths=("src/auth/**",)),
            TaskScope(task_id="billing", write_paths=("src/billing/**",)),
        ]
    )

    assert analysis.decision == TaskScopeDecision.ALLOW_PARALLEL
    assert analysis.conflicts == ()


def test_read_write_overlap_requires_serialization():
    analysis = analyze_task_scopes(
        [
            TaskScope(task_id="reader", read_paths=("src/auth/**",)),
            TaskScope(task_id="writer", write_paths=("src/auth/token.py",)),
        ]
    )

    assert analysis.decision == TaskScopeDecision.SERIALIZE
    assert analysis.conflicts[0].kind == TaskScopeConflictKind.READ_WRITE
    assert analysis.conflicts[0].task_ids == ("reader", "writer")
    assert analysis.conflicts[0].path_pairs == (
        ("src/auth/**", "src/auth/token.py"),
    )


def test_write_write_overlap_is_rejected():
    analysis = analyze_task_scopes(
        [
            TaskScope(task_id="api", write_paths=("src/auth/**",)),
            TaskScope(task_id="tests", write_paths=("src/auth/token.py",)),
        ]
    )

    assert analysis.decision == TaskScopeDecision.REJECT_CONFLICT
    assert analysis.conflicts[0].kind == TaskScopeConflictKind.WRITE_WRITE


def test_explicit_dependency_requires_serialization():
    analysis = analyze_task_scopes(
        [
            TaskScope(task_id="schema"),
            TaskScope(task_id="migration", depends_on=("schema",)),
        ]
    )

    assert analysis.decision == TaskScopeDecision.SERIALIZE
    assert analysis.conflicts[0].kind == TaskScopeConflictKind.DEPENDENCY
    assert analysis.conflicts[0].task_ids == ("migration", "schema")


def test_dependency_cycle_is_rejected():
    analysis = analyze_task_scopes(
        [
            TaskScope(task_id="a", depends_on=("b",)),
            TaskScope(task_id="b", depends_on=("c",)),
            TaskScope(task_id="c", depends_on=("a",)),
        ]
    )

    assert analysis.decision == TaskScopeDecision.REJECT_CONFLICT
    assert any(
        conflict.kind == TaskScopeConflictKind.DEPENDENCY_CYCLE
        and conflict.task_ids == ("a", "b", "c")
        for conflict in analysis.conflicts
    )


def test_unknown_and_self_dependencies_are_rejected():
    analysis = analyze_task_scopes(
        [
            TaskScope(task_id="self", depends_on=("self",)),
            TaskScope(task_id="unknown", depends_on=("missing",)),
        ]
    )

    assert analysis.decision == TaskScopeDecision.REJECT_CONFLICT
    assert {conflict.kind for conflict in analysis.conflicts} == {
        TaskScopeConflictKind.SELF_DEPENDENCY,
        TaskScopeConflictKind.UNKNOWN_DEPENDENCY,
    }


def test_paths_are_normalized_and_deduplicated():
    scope = TaskScope(
        task_id=" task ",
        read_paths=("./src\\auth\\token.py", "src/auth/token.py"),
        write_paths=("src/auth/**", "src/auth/**"),
        depends_on=(" base ", "base"),
    )

    assert scope.task_id == "task"
    assert scope.read_paths == ("src/auth/token.py",)
    assert scope.write_paths == ("src/auth/**",)
    assert scope.depends_on == ("base",)


@pytest.mark.parametrize(
    "path",
    ["", "/etc/passwd", "../secret", "src/*.py", "src/auth/?oken.py"],
)
def test_invalid_scope_paths_are_rejected(path):
    with pytest.raises(ValidationError):
        TaskScope(task_id="task", write_paths=(path,))


def test_workspace_wide_scope_overlaps_every_path():
    analysis = analyze_task_scopes(
        [
            TaskScope(task_id="all", write_paths=("**",)),
            TaskScope(task_id="one", write_paths=("README.md",)),
        ]
    )

    assert analysis.decision == TaskScopeDecision.REJECT_CONFLICT


def test_duplicate_task_ids_are_rejected():
    with pytest.raises(ValueError, match="task_id values must be unique"):
        analyze_task_scopes([TaskScope(task_id="same"), TaskScope(task_id="same")])
