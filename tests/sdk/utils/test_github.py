"""Tests for GitHub utility functions."""

from openhands.sdk.utils.github import ZWJ, sanitize_openhands_mentions


def test_sanitize_basic_mention():
    """Test basic @OpenHands mention is sanitized."""
    text = "Thanks @OpenHands for the help!"
    expected = f"Thanks @{ZWJ}OpenHands for the help!"
    assert sanitize_openhands_mentions(text) == expected


def test_sanitize_case_insensitive():
    """Test that mentions are sanitized regardless of case."""
    test_cases = [
        ("Check @OpenHands here", f"Check @{ZWJ}OpenHands here"),
        ("Check @openhands here", f"Check @{ZWJ}openhands here"),
        ("Check @OPENHANDS here", f"Check @{ZWJ}OPENHANDS here"),
        ("Check @oPeNhAnDs here", f"Check @{ZWJ}oPeNhAnDs here"),
    ]
    for input_text, expected in test_cases:
        assert sanitize_openhands_mentions(input_text) == expected


def test_sanitize_multiple_mentions():
    """Test multiple mentions in the same text."""
    text = "Both @OpenHands and @openhands should be sanitized"
    expected = f"Both @{ZWJ}OpenHands and @{ZWJ}openhands should be sanitized"
    assert sanitize_openhands_mentions(text) == expected


def test_sanitize_with_punctuation():
    """Test mentions followed by punctuation."""
    test_cases = [
        ("Thanks @OpenHands!", f"Thanks @{ZWJ}OpenHands!"),
        ("Hello @OpenHands.", f"Hello @{ZWJ}OpenHands."),
        ("See @OpenHands,", f"See @{ZWJ}OpenHands,"),
        ("By @OpenHands:", f"By @{ZWJ}OpenHands:"),
        ("From @OpenHands;", f"From @{ZWJ}OpenHands;"),
        ("Hi @OpenHands?", f"Hi @{ZWJ}OpenHands?"),
        ("Use @OpenHands)", f"Use @{ZWJ}OpenHands)"),
        ("Try (@OpenHands)", f"Try (@{ZWJ}OpenHands)"),
    ]
    for input_text, expected in test_cases:
        assert sanitize_openhands_mentions(input_text) == expected


def test_no_sanitize_partial_words():
    """Test that partial word matches are NOT sanitized."""
    test_cases = [
        "OpenHandsTeam",
        "MyOpenHands",
        "OpenHandsBot",
        "#OpenHands",
    ]
    for text in test_cases:
        # Partial words without @ should remain unchanged
        assert sanitize_openhands_mentions(text) == text


def test_no_op_cases():
    """Test cases where no sanitization should occur."""
    test_cases = [
        "",
        "No mentions here",
        "Just some text",
        "@GitHub",
        "@Other",
        "OpenHands without @",
    ]
    for text in test_cases:
        assert sanitize_openhands_mentions(text) == text


def test_sanitize_at_line_boundaries():
    """Test mentions at the start and end of lines."""
    test_cases = [
        ("@OpenHands at start", f"@{ZWJ}OpenHands at start"),
        ("at end @OpenHands", f"at end @{ZWJ}OpenHands"),
        ("@OpenHands", f"@{ZWJ}OpenHands"),
    ]
    for input_text, expected in test_cases:
        assert sanitize_openhands_mentions(input_text) == expected


def test_sanitize_multiline_text():
    """Test sanitization in multiline text."""
    text = """Hello @OpenHands!

This is a test with @openhands mentioned.

Thanks @OPENHANDS for everything!"""

    expected = f"""Hello @{ZWJ}OpenHands!

This is a test with @{ZWJ}openhands mentioned.

Thanks @{ZWJ}OPENHANDS for everything!"""

    assert sanitize_openhands_mentions(text) == expected


def test_sanitize_with_urls():
    """Test that URLs containing OpenHands are handled correctly."""
    test_cases = [
        # URL should not be sanitized
        ("Visit https://github.com/OpenHands", "Visit https://github.com/OpenHands"),
        # But mention should be sanitized
        (
            "See @OpenHands at https://github.com/OpenHands",
            f"See @{ZWJ}OpenHands at https://github.com/OpenHands",
        ),
    ]
    for input_text, expected in test_cases:
        assert sanitize_openhands_mentions(input_text) == expected


def test_sanitize_preserves_whitespace():
    """Test that whitespace is preserved correctly."""
    text = "  @OpenHands  \n  @openhands  "
    expected = f"  @{ZWJ}OpenHands  \n  @{ZWJ}openhands  "
    assert sanitize_openhands_mentions(text) == expected


def test_zwj_constant():
    """Test that ZWJ constant is correctly defined."""
    assert ZWJ == "\u200d"
    assert len(ZWJ) == 1
    assert ord(ZWJ) == 0x200D
