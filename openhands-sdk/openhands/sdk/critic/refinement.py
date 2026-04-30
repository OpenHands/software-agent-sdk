from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from openhands.sdk.critic.result import CriticResult


@dataclass(frozen=True)
class IterativeRefinementDecision:
    should_refine: bool
    triggered_issues: tuple[dict[str, Any], ...] = ()


def evaluate_iterative_refinement(
    critic_result: CriticResult | None,
    *,
    success_threshold: float,
) -> IterativeRefinementDecision:
    """Decide whether critic-driven iterative refinement should continue."""
    if critic_result is None:
        return IterativeRefinementDecision(should_refine=False)

    if critic_result.score < success_threshold:
        return IterativeRefinementDecision(should_refine=True)

    return IterativeRefinementDecision(should_refine=False)


def build_refinement_message(
    critic_result: CriticResult,
    iteration: int,
    *,
    max_iterations: int | None = None,
) -> str:
    """Build a follow-up prompt for iterative refinement."""
    score_percent = critic_result.score * 100
    if max_iterations is None:
        iteration_label = f"iteration {iteration}"
    else:
        iteration_label = f"iteration {iteration}/{max_iterations}"

    lines = [
        (
            "The task appears incomplete "
            f"({iteration_label}, predicted success likelihood: {score_percent:.1f}%)."
        ),
        "",
    ]

    lines.extend(
        [
            "Please review what you've done and verify each requirement is met.",
            "List what's working and what needs fixing, then complete the task.",
        ]
    )
    return "\n".join(lines)
