import json

from openhands.sdk.critic.result import CriticResult


def test_format_critic_result_with_json_message():
    """Test formatting critic result with JSON probabilities.

    When no metadata with categorized_features is provided, the raw JSON
    message is displayed as-is in the fallback format.
    """
    probs_dict = {
        "sentiment_neutral": 0.7612602710723877,
        "direction_change": 0.5926198959350586,
        "success": 0.5067704319953918,
        "sentiment_positive": 0.18567389249801636,
        "correction": 0.14625290036201477,
    }
    critic_result = CriticResult(score=0.507, message=json.dumps(probs_dict))

    # Test visualize property
    formatted = critic_result.visualize
    text = formatted.plain

    # Should display star rating with percentage
    assert "Critic: agent success likelihood" in text
    assert "★★★☆☆" in text  # Score 0.507 rounds to 3 stars
    assert "(50.7%)" in text

    # Without metadata, the raw JSON message is displayed as-is
    assert "sentiment_neutral" in text
    assert "direction_change" in text
    assert "success" in text
    assert "correction" in text


def test_format_critic_result_with_plain_message():
    """Test formatting critic result with plain text message."""
    critic_result = CriticResult(score=0.75, message="This is a plain text message")

    formatted = critic_result.visualize
    text = formatted.plain

    # Should display star rating
    assert "Critic: agent success likelihood" in text
    assert "★★★★☆" in text  # Score 0.75 rounds to 4 stars
    # Should display plain text message
    assert "This is a plain text message" in text


def test_format_critic_result_without_message():
    """Test formatting critic result without message."""
    critic_result = CriticResult(score=0.65, message=None)

    formatted = critic_result.visualize
    text = formatted.plain

    # Should display star rating
    assert "Critic: agent success likelihood" in text
    assert "★★★☆☆" in text  # Score 0.65 rounds to 3 stars
    # Should be compact - just a few lines
    assert text.count("\n") <= 3


def test_visualize_consistency():
    """Test that visualize property consistently formats the result.

    When no metadata with categorized_features is provided, the raw JSON
    message is displayed as-is.
    """
    probs_dict = {
        "success": 0.8,
        "sentiment_positive": 0.7,
        "sentiment_neutral": 0.2,
    }
    critic_result = CriticResult(score=0.8, message=json.dumps(probs_dict))

    formatted = critic_result.visualize.plain

    # Should display star rating
    assert "Critic: agent success likelihood" in formatted
    assert "★★★★☆" in formatted  # Score 0.8 rounds to 4 stars
    # Without metadata, the raw JSON message is displayed as-is
    assert "success" in formatted
    assert "sentiment_positive" in formatted
    assert "sentiment_neutral" in formatted


def test_format_critic_result_sorting():
    """Test that raw JSON message is displayed when no metadata is provided.

    When no metadata with categorized_features is provided, the raw JSON
    message is displayed as-is without filtering or sorting.
    """
    probs_dict = {
        "low": 0.1,
        "medium": 0.5,
        "high": 0.9,
        "very_low": 0.01,
    }
    critic_result = CriticResult(score=0.5, message=json.dumps(probs_dict))

    formatted = critic_result.visualize
    text = formatted.plain

    # Without metadata, all keys from the raw JSON message are displayed
    assert "high" in text
    assert "medium" in text
    assert "low" in text
    assert "very_low" in text


def test_color_highlighting():
    """Test that the visualize output has appropriate styling.

    When no metadata with categorized_features is provided, the raw JSON
    message is displayed as-is. The star rating and header still have styling.
    """
    probs_dict = {
        "critical": 0.85,
        "important": 0.65,
        "notable": 0.40,
        "medium": 0.15,
        "minimal": 0.02,
    }
    critic_result = CriticResult(score=0.5, message=json.dumps(probs_dict))

    formatted = critic_result.visualize

    # Without metadata, all keys from the raw JSON message are displayed
    text = formatted.plain
    assert "critical" in text
    assert "important" in text
    assert "notable" in text
    assert "medium" in text
    assert "minimal" in text

    # Verify spans contain style information for the star rating and header
    # Rich Text objects have spans with (start, end, style) tuples
    spans = list(formatted.spans)
    assert len(spans) > 0, "Should have styled spans"

    # Check that different styles are applied (just verify they exist)
    styles = {span.style for span in spans if span.style}
    assert len(styles) > 1, "Should have multiple different styles"


def test_star_rating():
    """Test that scores map to correct star ratings.

    Each star represents 20%, using round() for conversion.
    Python uses banker's rounding (round half to even).
    """
    # 5 stars
    assert CriticResult._get_star_rating(1.0) == "★★★★★"

    # 4 stars
    assert CriticResult._get_star_rating(0.9) == "★★★★☆"  # 4.5 rounds to 4 (banker's)
    assert CriticResult._get_star_rating(0.8) == "★★★★☆"
    assert CriticResult._get_star_rating(0.7) == "★★★★☆"  # 3.5 rounds to 4 (banker's)

    # 3 stars
    assert CriticResult._get_star_rating(0.6) == "★★★☆☆"
    assert CriticResult._get_star_rating(0.55) == "★★★☆☆"

    # 2 stars
    assert CriticResult._get_star_rating(0.5) == "★★☆☆☆"  # 2.5 rounds to 2 (banker's)
    assert CriticResult._get_star_rating(0.4) == "★★☆☆☆"
    assert CriticResult._get_star_rating(0.35) == "★★☆☆☆"

    # 1 star
    assert CriticResult._get_star_rating(0.3) == "★★☆☆☆"  # 1.5 rounds to 2 (banker's)
    assert CriticResult._get_star_rating(0.2) == "★☆☆☆☆"
    assert CriticResult._get_star_rating(0.15) == "★☆☆☆☆"

    # 0 stars
    assert CriticResult._get_star_rating(0.1) == "☆☆☆☆☆"  # 0.5 rounds to 0 (banker's)
    assert CriticResult._get_star_rating(0.0) == "☆☆☆☆☆"


def test_star_style():
    """Test that star styles are correct based on score."""
    # Green for >= 0.6
    assert CriticResult._get_star_style(0.6) == "green"
    assert CriticResult._get_star_style(1.0) == "green"

    # Yellow for 0.4-0.6
    assert CriticResult._get_star_style(0.4) == "yellow"
    assert CriticResult._get_star_style(0.59) == "yellow"

    # Red for < 0.4
    assert CriticResult._get_star_style(0.0) == "red"
    assert CriticResult._get_star_style(0.39) == "red"


def test_visualize_with_categorized_features():
    """Test visualization with categorized features from metadata."""
    categorized = {
        "sentiment": {
            "predicted": "Neutral",
            "probability": 0.77,
            "all": {"positive": 0.10, "neutral": 0.77, "negative": 0.13},
        },
        "agent_behavioral_issues": [
            {
                "name": "loop_behavior",
                "display_name": "Loop Behavior",
                "probability": 0.85,
            },
            {
                "name": "insufficient_testing",
                "display_name": "Insufficient Testing",
                "probability": 0.57,
            },
        ],
        "user_followup_patterns": [
            {
                "name": "direction_change",
                "display_name": "Direction Change",
                "probability": 0.59,
            },
        ],
        "infrastructure_issues": [],
        "other": [],
    }

    result = CriticResult(
        score=0.65,
        message="test",
        metadata={"categorized_features": categorized},
    )

    text = result.visualize.plain

    # Should display star rating
    assert "Critic: agent success likelihood" in text
    assert "★★★☆☆" in text  # Score 0.65 rounds to 3 stars
    assert "(65.0%)" in text

    # Should display issues with likelihood percentages
    assert "Potential Issues:" in text
    assert "Loop Behavior" in text
    assert "(likelihood 85%)" in text
    assert "Insufficient Testing" in text
    assert "(likelihood 57%)" in text

    # Should display follow-up patterns
    assert "Likely Follow-up:" in text
    assert "Direction Change" in text
    assert "(likelihood 59%)" in text

    # Should NOT display sentiment (removed)
    assert "Expected User Sentiment" not in text
