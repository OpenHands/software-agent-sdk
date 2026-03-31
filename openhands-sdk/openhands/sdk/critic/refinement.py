from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from openhands.sdk.critic.result import CriticResult


DEFAULT_ISSUE_THRESHOLD = 0.75


@dataclass(frozen=True)
class IterativeRefinementDecision:
    should_refine: bool
    triggered_issues: tuple[dict[str, Any], ...] = ()


def _format_feature_for_prompt(feature: dict[str, Any]) -> str:
    name = feature.get("display_name", feature.get("name", "Unknown"))
    probability = feature.get("probability", 0)
    return f"{name} ({probability:.0%})"


def get_high_probability_issues(
    critic_result: CriticResult,
    issue_threshold: float = DEFAULT_ISSUE_THRESHOLD,
) -> tuple[dict[str, Any], ...]:
    """Return critic-detected agent issues above the refinement threshold."""
    if not critic_result.metadata:
        return ()

    categorized = critic_result.metadata.get("categorized_features", {})
    if not categorized:
        return ()

    high_probability_issues = [
        issue
        for issue in categorized.get("agent_behavioral_issues", [])
        if issue.get("probability", 0) >= issue_threshold
    ]
    high_probability_issues.sort(
        key=lambda issue: issue.get("probability", 0), reverse=True
    )
    return tuple(high_probability_issues)


def evaluate_iterative_refinement(
    critic_result: CriticResult | None,
    *,
    success_threshold: float,
    issue_threshold: float = DEFAULT_ISSUE_THRESHOLD,
) -> IterativeRefinementDecision:
    """Decide whether critic-driven iterative refinement should continue."""
    if critic_result is None:
        return IterativeRefinementDecision(should_refine=False)

    high_probability_issues = get_high_probability_issues(
        critic_result, issue_threshold
    )
    if critic_result.score < success_threshold or high_probability_issues:
        return IterativeRefinementDecision(
            should_refine=True,
            triggered_issues=high_probability_issues,
        )

    return IterativeRefinementDecision(should_refine=False)


def build_refinement_message(
    critic_result: CriticResult,
    iteration: int,
    *,
    max_iterations: int | None = None,
    issue_threshold: float = DEFAULT_ISSUE_THRESHOLD,
    triggered_issues: tuple[dict[str, Any], ...] | None = None,
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

    if triggered_issues is None:
        triggered_issues = get_high_probability_issues(critic_result, issue_threshold)

    if triggered_issues:
        lines.append("**Detected issues requiring attention:**")
        for issue in triggered_issues:
            lines.append(f"- {_format_feature_for_prompt(issue)}")
        lines.append("")

    lines.extend(
        [
            "Please review what you've done and verify each requirement is met.",
            "List what's working and what needs fixing, then complete the task.",
        ]
    )
    return "\n".join(lines)
