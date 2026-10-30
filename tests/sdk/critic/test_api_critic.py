"""Edge-case tests for APIBasedCritic evaluation and refinement decisions."""

import pytest

from openhands.sdk.critic.base import CriticResult, IterativeRefinementConfig
from openhands.sdk.critic.impl.api import APIBasedCritic
from openhands.sdk.event import MessageEvent, SystemPromptEvent
from openhands.sdk.llm import Message, TextContent


def _critic(**kwargs) -> APIBasedCritic:
    return APIBasedCritic(api_key="key", server_url="http://critic.invalid", **kwargs)


def _user_message() -> MessageEvent:
    return MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="hi")]),
    )


def _result_with_issue(probability: float, score: float = 0.9) -> CriticResult:
    return CriticResult(
        score=score,
        message=None,
        metadata={
            "categorized_features": {
                "agent_behavioral_issues": [
                    {"name": "loop_behavior", "probability": probability}
                ]
            }
        },
    )


def test_evaluate_requires_a_system_prompt_event():
    with pytest.raises(ValueError, match="SystemPromptEvent is required"):
        _critic().evaluate([_user_message()])


def test_evaluate_requires_tools_in_the_system_prompt():
    system = SystemPromptEvent(system_prompt=TextContent(text="sys"), tools=[])

    with pytest.raises(ValueError, match="requires tools"):
        _critic().evaluate([system, _user_message()])


def test_high_probability_issue_does_not_refine_without_refinement_config():
    assert not _critic().should_refine(_result_with_issue(0.99))


@pytest.mark.parametrize(("probability", "expected"), [(0.75, True), (0.7499, False)])
def test_issue_threshold_is_inclusive(probability, expected):
    critic = _critic(iterative_refinement=IterativeRefinementConfig())

    assert critic.should_refine(_result_with_issue(probability)) is expected


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"categorized_features": "not-a-dict"},
        {"categorized_features": {"agent_behavioral_issues": ["not-a-dict"]}},
    ],
)
def test_malformed_metadata_falls_back_to_the_score(metadata):
    critic = _critic(iterative_refinement=IterativeRefinementConfig())
    passing = CriticResult(score=0.9, message=None, metadata=metadata)

    assert not critic.should_refine(passing)
    failing = CriticResult(score=0.1, message=None, metadata=metadata)
    assert critic.should_refine(failing)


def test_followup_prompt_without_metadata_has_only_the_generic_guidance():
    prompt = _critic().get_followup_prompt(
        CriticResult(score=0.25, message=None), iteration=2
    )

    assert "iteration 2" in prompt
    assert "25.0%" in prompt
    assert "Potential agent issues" not in prompt
    assert prompt.endswith("then complete the task.")


def test_followup_prompt_lists_features_without_display_names():
    prompt = _critic().get_followup_prompt(_result_with_issue(0.8), iteration=1)

    assert "Potential agent issues: loop_behavior (80%)" in prompt
