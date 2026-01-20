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

    # Should display quality assessment instead of raw score
    assert "Quality Assessment:" in text
    assert "Fair" in text  # Score 0.507 maps to "Fair"

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

    # Should display quality assessment
    assert "Quality Assessment:" in text
    assert "Good" in text  # Score 0.75 maps to "Good"
    # Should display plain text message
    assert "This is a plain text message" in text


def test_format_critic_result_without_message():
    """Test formatting critic result without message."""
    critic_result = CriticResult(score=0.65, message=None)

    formatted = critic_result.visualize
    text = formatted.plain

    # Should display quality assessment
    assert "Quality Assessment:" in text
    assert "Good" in text  # Score 0.65 maps to "Good"
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

    # Should display quality assessment
    assert "Quality Assessment:" in formatted
    assert "Excellent" in formatted  # Score 0.8 maps to "Excellent"
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
    message is displayed as-is. The quality assessment and header still have styling.
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

    # Verify spans contain style information for the quality assessment and header
    # Rich Text objects have spans with (start, end, style) tuples
    spans = list(formatted.spans)
    assert len(spans) > 0, "Should have styled spans"

    # Check that different styles are applied (just verify they exist)
    styles = {span.style for span in spans if span.style}
    assert len(styles) > 1, "Should have multiple different styles"


def test_quality_assessment_levels():
    """Test that scores map to correct quality assessment levels."""
    # Excellent (>= 0.8)
    assert CriticResult._get_quality_assessment(0.8)[0] == "Excellent"
    assert CriticResult._get_quality_assessment(1.0)[0] == "Excellent"

    # Good (>= 0.6)
    assert CriticResult._get_quality_assessment(0.6)[0] == "Good"
    assert CriticResult._get_quality_assessment(0.79)[0] == "Good"

    # Fair (>= 0.5)
    assert CriticResult._get_quality_assessment(0.5)[0] == "Fair"
    assert CriticResult._get_quality_assessment(0.59)[0] == "Fair"

    # Needs Improvement (>= 0.3)
    assert CriticResult._get_quality_assessment(0.3)[0] == "Needs Improvement"
    assert CriticResult._get_quality_assessment(0.49)[0] == "Needs Improvement"

    # Poor (< 0.3)
    assert CriticResult._get_quality_assessment(0.0)[0] == "Poor"
    assert CriticResult._get_quality_assessment(0.29)[0] == "Poor"


def test_confidence_levels():
    """Test that probabilities map to correct confidence labels."""
    # High (>= 0.7)
    assert CriticResult._get_confidence_label(0.7)[0] == "High"
    assert CriticResult._get_confidence_label(1.0)[0] == "High"

    # Medium (>= 0.5)
    assert CriticResult._get_confidence_label(0.5)[0] == "Medium"
    assert CriticResult._get_confidence_label(0.69)[0] == "Medium"

    # Low (< 0.5)
    assert CriticResult._get_confidence_label(0.0)[0] == "Low"
    assert CriticResult._get_confidence_label(0.49)[0] == "Low"


def test_sentiment_emojis():
    """Test that sentiments map to correct emojis."""
    assert CriticResult._get_sentiment_emoji("Positive") == "😊"
    assert CriticResult._get_sentiment_emoji("Neutral") == "😐"
    assert CriticResult._get_sentiment_emoji("Negative") == "😟"
    assert CriticResult._get_sentiment_emoji("Unknown") == ""


def test_visualize_with_categorized_features():
    """Test visualization with categorized features from metadata."""
    categorized = {
        "sentiment": {
            "predicted": "Neutral",
            "probability": 0.77,
            "all": {"positive": 0.10, "neutral": 0.77, "negative": 0.13},
        },
        "agent_behavioral_issues": [
            {"name": "loop_behavior", "display_name": "Loop Behavior", "probability": 0.85},
            {"name": "insufficient_testing", "display_name": "Insufficient Testing", "probability": 0.57},
        ],
        "user_followup_patterns": [
            {"name": "direction_change", "display_name": "Direction Change", "probability": 0.59},
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

    # Should display quality assessment
    assert "Quality Assessment:" in text
    assert "Good" in text

    # Should display sentiment with emoji
    assert "Expected User Response:" in text
    assert "😐" in text
    assert "Neutral" in text

    # Should display issues with confidence levels
    assert "Potential Issues:" in text
    assert "Loop Behavior" in text
    assert "(High)" in text
    assert "Insufficient Testing" in text
    assert "(Medium)" in text

    # Should display follow-up patterns
    assert "Likely Follow-up:" in text
    assert "Direction Change" in text
