from openhands.sdk.critic.base import IterativeRefinementConfig
from openhands.sdk.critic.refinement import (
    IterativeRefinementDecision,
    build_refinement_message,
    evaluate_iterative_refinement,
)
from openhands.sdk.critic.result import CriticResult


def test_evaluate_iterative_refinement_triggers_on_low_score() -> None:
    result = CriticResult(
        score=0.4,
        message="Low score",
    )

    decision = evaluate_iterative_refinement(result, success_threshold=0.6)

    assert decision == IterativeRefinementDecision(should_refine=True)


def test_evaluate_iterative_refinement_ignores_api_metadata_by_default() -> None:
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

    decision = evaluate_iterative_refinement(result, success_threshold=0.6)

    assert decision == IterativeRefinementDecision(should_refine=False)


def test_iterative_refinement_config_evaluates_score_threshold() -> None:
    result = CriticResult(
        score=0.4,
        message="Low score",
    )
    config = IterativeRefinementConfig(
        success_threshold=0.6,
        max_iterations=3,
    )
    decision = config.evaluate(result)

    assert decision.should_refine is True


def test_build_refinement_message_is_generic() -> None:
    result = CriticResult(score=0.3, message="Low score")

    prompt = build_refinement_message(result, 1, max_iterations=3)

    assert "iteration 1/3" in prompt
    assert "Detected issues requiring attention" not in prompt
