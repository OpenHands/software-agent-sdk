"""Tests for critic feature categorization."""

import math

from openhands.sdk.critic.impl.api.taxonomy import categorize_features


def test_large_sentiment_logits_do_not_overflow():
    result = categorize_features(
        {"sentiment_positive": 1000.0, "sentiment_negative": 0.0}
    )

    assert result["sentiment"]["predicted"] == "Positive"
    assert math.isclose(result["sentiment"]["probability"], 1.0)
