import json

from openhands.sdk.critic.result import CriticResult


def test_format_critic_result_with_json_message():
    """Test formatting critic result with JSON probabilities."""
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

    # Should display overall score with 4 digits inline
    assert "0.5070" in text

    # Sentiments should be normalized and shown as percentages
    # sentiment_neutral and sentiment_positive should be normalized
    # Total sentiment: 0.7613 + 0.1857 = 0.9470
    # neutral: 0.7613/0.9470 = ~80.4%, positive: 0.1857/0.9470 = ~19.6%
    assert "neutral" in text
    assert "positive" in text
    # Check for percentage format
    assert "%" in text

    # Other metrics should be shown with 2 digit precision
    assert "direction_change" in text
    assert "success" in text
    assert "correction" in text


def test_format_critic_result_with_plain_message():
    """Test formatting critic result with plain text message."""
    critic_result = CriticResult(score=0.75, message="This is a plain text message")

    formatted = critic_result.visualize
    text = formatted.plain

    # Should display overall score
    assert "0.7500" in text
    # Should display plain text message
    assert "This is a plain text message" in text


def test_format_critic_result_without_message():
    """Test formatting critic result without message."""
    critic_result = CriticResult(score=0.65, message=None)

    formatted = critic_result.visualize
    text = formatted.plain

    # Should only display overall score
    assert "0.6500" in text
    # Should be compact - just a few lines
    assert text.count("\n") <= 3


def test_visualize_consistency():
    """Test that visualize property consistently formats the result."""
    probs_dict = {
        "success": 0.8,
        "sentiment_positive": 0.7,
        "sentiment_neutral": 0.2,
    }
    critic_result = CriticResult(score=0.8, message=json.dumps(probs_dict))

    formatted = critic_result.visualize.plain

    # Should contain overall score
    assert "0.8000" in formatted
    # Should contain success metric
    assert "success" in formatted
    # Sentiments should be normalized
    # Total: 0.7 + 0.2 = 0.9
    # positive: 0.7/0.9 = 77.8%, neutral: 0.2/0.9 = 22.2%
    assert "positive" in formatted
    assert "neutral" in formatted
    assert "%" in formatted


def test_format_critic_result_sorting():
    """Test that probabilities are sorted in descending order."""
    probs_dict = {
        "low": 0.1,
        "medium": 0.5,
        "high": 0.9,
        "very_low": 0.01,
    }
    critic_result = CriticResult(score=0.5, message=json.dumps(probs_dict))

    formatted = critic_result.visualize
    text = formatted.plain

    # Metrics should be sorted in descending order
    # high (0.9), medium (0.5), low (0.1) should appear
    # very_low (0.01) should be filtered out (below DISPLAY_THRESHOLD of 0.1)
    assert "high" in text
    assert "medium" in text
    assert "low" in text
    assert "very_low" not in text  # Below threshold

    # Verify sorting order: high should appear before medium, medium before low
    high_pos = text.find("high")
    medium_pos = text.find("medium")
    low_pos = text.find("low:")  # Use "low:" to avoid matching "very_low"

    assert high_pos < medium_pos < low_pos


def test_color_highlighting():
    """Test that probabilities have appropriate color styling."""
    probs_dict = {
        "critical": 0.85,  # Should be red bold (>= 0.7)
        "important": 0.65,  # Should be red (>= 0.5)
        "notable": 0.40,  # Should be yellow (>= 0.3)
        "medium": 0.15,  # Should be white (>= 0.1)
        "minimal": 0.02,  # Should be filtered out (< 0.1)
    }
    critic_result = CriticResult(score=0.5, message=json.dumps(probs_dict))

    formatted = critic_result.visualize

    # Check that the Text object has the expected content (2 decimal places now)
    text = formatted.plain
    assert "critical" in text
    assert "important" in text
    assert "notable" in text
    assert "medium" in text
    assert "minimal" not in text  # Below threshold, should be filtered

    # Verify spans contain style information
    # Rich Text objects have spans with (start, end, style) tuples
    spans = list(formatted.spans)
    assert len(spans) > 0, "Should have styled spans"

    # Check that different styles are applied (just verify they exist)
    styles = {span.style for span in spans if span.style}
    assert len(styles) > 1, "Should have multiple different styles"
