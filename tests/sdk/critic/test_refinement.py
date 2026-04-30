from openhands.sdk.critic.base import IterativeRefinementConfig
from openhands.sdk.critic.refinement import (
    IterativeRefinementDecision,
    build_refinement_message,
    evaluate_iterative_refinement,
    get_high_probability_issues,
)
from openhands.sdk.critic.result import CriticResult


def test_get_high_probability_issues_filters_and_sorts() -> None:
    result = CriticResult(
        score=0.8,
        message="Good overall score",
        metadata={
            "categorized_features": {
                "agent_behavioral_issues": [
                    {"name": "insufficient_testing", "probability": 0.8},
                    {"name": "loop_behavior", "probability": 0.9},
                    {"name": "scope_creep", "probability": 0.6},
                ]
            }
        },
    )

    issues = get_high_probability_issues(result, issue_threshold=0.75)

    assert [issue["name"] for issue in issues] == [
        "loop_behavior",
        "insufficient_testing",
    ]


def test_evaluate_iterative_refinement_triggers_on_high_probability_issue() -> None:
    result = CriticResult(
        score=0.85,
        message="High score with issue",
        metadata={
            "categorized_features": {
                "agent_behavioral_issues": [
                    {
                        "name": "insufficient_testing",
                        "display_name": "Insufficient Testing",
                        "probability": 0.82,
                    }
                ]
            }
        },
    )

    decision = evaluate_iterative_refinement(
        result,
        success_threshold=0.6,
        issue_threshold=0.75,
    )

    assert decision == IterativeRefinementDecision(
        should_refine=True,
        triggered_issues=(
            {
                "name": "insufficient_testing",
                "display_name": "Insufficient Testing",
                "probability": 0.82,
            },
        ),
    )


def test_iterative_refinement_config_builds_followup_prompt_with_issues() -> None:
    result = CriticResult(
        score=0.4,
        message="Low score",
        metadata={
            "categorized_features": {
                "agent_behavioral_issues": [
                    {
                        "name": "insufficient_testing",
                        "display_name": "Insufficient Testing",
                        "probability": 0.8,
                    }
                ]
            }
        },
    )
    config = IterativeRefinementConfig(
        success_threshold=0.6,
        issue_threshold=0.75,
        max_iterations=3,
    )
    decision = config.evaluate(result)

    prompt = config.build_followup_prompt(result, 2, decision=decision)

    assert decision.should_refine is True
    assert "iteration 2/3" in prompt
    assert "Detected issues requiring attention" in prompt
    assert "Insufficient Testing (80%)" in prompt


def test_build_refinement_message_omits_issue_section_when_none_triggered() -> None:
    result = CriticResult(score=0.3, message="Low score")

    prompt = build_refinement_message(result, 1, max_iterations=3)

    assert "iteration 1/3" in prompt
    assert "Detected issues requiring attention" not in prompt
