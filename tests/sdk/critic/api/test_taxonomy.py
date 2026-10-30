"""Tests for critic taxonomy categorization."""

import math

import pytest

from openhands.sdk.critic.impl.api.taxonomy import categorize_features


def test_empty_probabilities_yield_empty_categories():
    result = categorize_features({})

    assert result["sentiment"] is None
    for key in (
        "agent_behavioral_issues",
        "user_followup_patterns",
        "infrastructure_issues",
        "other",
    ):
        assert result[key] == []


def test_threshold_is_inclusive_and_drops_lower_scores():
    result = categorize_features(
        {"loop_behavior": 0.2, "scope_creep": 0.19999}, display_threshold=0.2
    )

    assert [f["name"] for f in result["agent_behavioral_issues"]] == ["loop_behavior"]


def test_success_and_general_context_are_never_listed():
    result = categorize_features(
        {"success": 0.99, "user_goal_summary": 0.9, "overall_sentiment": 0.9}
    )

    assert all(
        result[key] == []
        for key in (
            "agent_behavioral_issues",
            "user_followup_patterns",
            "infrastructure_issues",
            "other",
        )
    )


def test_unknown_features_go_to_other_sorted_by_probability():
    result = categorize_features({"new_label_a": 0.3, "new_label_b": 0.9})

    assert [f["name"] for f in result["other"]] == ["new_label_b", "new_label_a"]
    assert result["other"][0]["display_name"] == "New Label B"


def test_sentiment_is_normalized_to_a_distribution():
    result = categorize_features(
        {"sentiment_positive": 2.0, "sentiment_neutral": 0.0, "sentiment_negative": 0.0}
    )

    sentiment = result["sentiment"]
    assert sentiment["predicted"] == "Positive"
    assert math.isclose(sum(sentiment["all"].values()), 1.0)
    # Sentiment logits are never listed as ordinary features.
    assert result["other"] == []


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG: _softmax_normalize calls math.exp on raw logits without "
        "subtracting the max, so a large logit raises OverflowError"
    ),
)
def test_large_sentiment_logits_do_not_overflow():
    result = categorize_features(
        {"sentiment_positive": 1000.0, "sentiment_negative": 0.0}
    )

    assert result["sentiment"]["predicted"] == "Positive"
    assert math.isclose(result["sentiment"]["probability"], 1.0)
