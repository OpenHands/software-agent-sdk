import json

from openhands.sdk.critic.result import CriticResult


def test_format_critic_result_with_json_message():
    """Test formatting critic result with JSON probabilities.

    When no metadata with categorized_features is provided, the raw JSON
    message is displayed as-is.
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

    # Should display overall score with 2 decimal places inline
    assert "0.51" in text

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

    # Should display overall score with 2 decimal places
    assert "0.75" in text
    # Should display plain text message
    assert "This is a plain text message" in text


def test_format_critic_result_without_message():
    """Test formatting critic result without message."""
    critic_result = CriticResult(score=0.65, message=None)

    formatted = critic_result.visualize
    text = formatted.plain

    # Should only display overall score with 2 decimal places
    assert "0.65" in text
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

    # Should contain overall score with 2 decimal places
    assert "0.80" in formatted
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
    message is displayed as-is. The score and header still have styling.
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

    # Verify spans contain style information for the score and header
    # Rich Text objects have spans with (start, end, style) tuples
    spans = list(formatted.spans)
    assert len(spans) > 0, "Should have styled spans"

    # Check that different styles are applied (just verify they exist)
    styles = {span.style for span in spans if span.style}
    assert len(styles) > 1, "Should have multiple different styles"
