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

    # Should display overall score with 4 digits
    assert "Overall: 0.5070" in text

    # First probability line should be sentiment_neutral (highest)
    assert "sentiment_neutral: 0.7613" in text
    # Second should be direction_change
    assert "direction_change: 0.5926" in text

    # Check 4 digit precision
    assert "0.7613" in text  # rounded from 0.7612602710723877
    assert "0.5926" in text  # rounded from 0.5926198959350586
    assert "0.5068" in text  # rounded from 0.5067704319953918


def test_format_critic_result_with_plain_message():
    """Test formatting critic result with plain text message."""
    critic_result = CriticResult(score=0.75, message="This is a plain text message")

    formatted = critic_result.visualize
    text = formatted.plain

    # Should display overall score
    assert "Overall: 0.7500" in text
    # Should display plain text message
    assert "This is a plain text message" in text


def test_format_critic_result_without_message():
    """Test formatting critic result without message."""
    critic_result = CriticResult(score=0.65, message=None)

    formatted = critic_result.visualize
    text = formatted.plain

    # Should only display overall score
    assert "Overall: 0.6500" in text
    # Should not have extra content
    assert text.count("\n") <= 3  # Header, score, maybe empty line


def test_visualize_consistency():
    """Test that visualize property consistently formats the result."""
    probs_dict = {
        "success": 0.8,
        "sentiment_positive": 0.7,
        "sentiment_neutral": 0.2,
    }
    critic_result = CriticResult(score=0.8, message=json.dumps(probs_dict))

    formatted = critic_result.visualize.plain

    # Should contain all expected information
    assert "Overall: 0.8000" in formatted
    assert "success: 0.8000" in formatted
    assert "sentiment_positive: 0.7000" in formatted
    assert "sentiment_neutral: 0.2000" in formatted


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

    # Find positions of each field
    high_pos = text.find("high: 0.9000")
    medium_pos = text.find("medium: 0.5000")
    low_pos = text.find("low: 0.1000")
    very_low_pos = text.find("very_low: 0.0100")

    # Verify sorting order
    assert high_pos < medium_pos < low_pos < very_low_pos


def test_color_highlighting():
    """Test that probabilities have appropriate color styling."""
    probs_dict = {
        "critical": 0.85,  # Should be red bold (>= 0.7)
        "important": 0.65,  # Should be red (>= 0.5)
        "notable": 0.40,  # Should be yellow (>= 0.3)
        "medium": 0.15,  # Should be white (>= 0.1)
        "minimal": 0.02,  # Should be dim (< 0.1)
    }
    critic_result = CriticResult(score=0.5, message=json.dumps(probs_dict))

    formatted = critic_result.visualize

    # Check that the Text object has the expected content
    text = formatted.plain
    assert "critical: 0.8500" in text
    assert "important: 0.6500" in text
    assert "notable: 0.4000" in text
    assert "medium: 0.1500" in text
    assert "minimal: 0.0200" in text

    # Verify spans contain style information
    # Rich Text objects have spans with (start, end, style) tuples
    spans = list(formatted.spans)
    assert len(spans) > 0, "Should have styled spans"

    # Check that different styles are applied (just verify they exist)
    styles = {span.style for span in spans if span.style}
    assert len(styles) > 1, "Should have multiple different styles"
