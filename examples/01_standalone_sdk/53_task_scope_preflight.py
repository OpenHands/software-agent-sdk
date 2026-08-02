"""Preflight parallel sub-agent work with declared task scopes."""

from openhands.tools.task import (
    TaskScope,
    TaskScopeDecision,
    analyze_task_scopes,
)


safe = analyze_task_scopes(
    [
        TaskScope(task_id="auth", write_paths=("src/auth/**",)),
        TaskScope(task_id="billing", write_paths=("src/billing/**",)),
    ]
)
assert safe.decision == TaskScopeDecision.ALLOW_PARALLEL

conflicting = analyze_task_scopes(
    [
        TaskScope(task_id="auth-api", write_paths=("src/auth/**",)),
        TaskScope(task_id="auth-tests", write_paths=("src/auth/token.py",)),
    ]
)
assert conflicting.decision == TaskScopeDecision.REJECT_CONFLICT

print(f"safe decision: {safe.decision.value}")
print(f"conflicting decision: {conflicting.decision.value}")
for conflict in conflicting.conflicts:
    print(
        f"{conflict.kind.value}: tasks={conflict.task_ids} "
        f"paths={conflict.path_pairs}"
    )
print("EXAMPLE_COST: 0.0")
