"""Tests for BrowserObservation wrapper behavior."""

from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.llm.message import ImageContent, TextContent
from openhands.tools.browser_use.definition import BrowserObservation


def test_browser_observation_basic_output():
    """Test basic BrowserObservation creation with output."""
    observation = BrowserObservation.from_text(text="Test output")

    assert observation.text == "Test output"
    assert observation.is_error is False
    assert observation.screenshot_data is None


def test_browser_observation_with_error():
    """Test BrowserObservation with error."""
    observation = BrowserObservation.from_text(text="Test error", is_error=True)

    assert observation.text == "Test error"
    assert observation.is_error is True
    assert observation.screenshot_data is None


def test_browser_observation_with_screenshot():
    """Test BrowserObservation with screenshot data."""
    screenshot_data = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChAI9jU77zgAAAABJRU5ErkJggg=="  # noqa: E501
    observation = BrowserObservation.from_text(
        text="Screenshot taken", screenshot_data=screenshot_data
    )

    assert observation.text == "Screenshot taken"
    assert observation.is_error is False
    assert observation.screenshot_data == screenshot_data


def test_browser_observation_to_llm_content_text_only():
    """Test to_llm_content property with text only."""
    observation = BrowserObservation.from_text(text="Test output")
    agent_obs = observation.to_llm_content

    assert len(agent_obs) == 1
    assert isinstance(agent_obs[0], TextContent)
    assert agent_obs[0].text == "Test output"


def test_browser_observation_to_llm_content_with_screenshot():
    """Test to_llm_content property with screenshot."""
    screenshot_data = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChAI9jU77zgAAAABJRU5ErkJggg=="  # noqa: E501
    observation = BrowserObservation.from_text(
        text="Screenshot taken", screenshot_data=screenshot_data
    )
    agent_obs = observation.to_llm_content

    assert len(agent_obs) == 2
    assert isinstance(agent_obs[0], TextContent)
    assert agent_obs[0].text == "Screenshot taken"
    assert isinstance(agent_obs[1], ImageContent)
    assert len(agent_obs[1].image_urls) == 1
    assert agent_obs[1].image_urls[0].startswith("data:image/png;base64,")
    assert screenshot_data in agent_obs[1].image_urls[0]


def test_browser_observation_to_llm_content_with_error():
    """Test to_llm_content property with error."""
    observation = BrowserObservation.from_text(text="Test error", is_error=True)
    agent_obs = observation.to_llm_content

    assert len(agent_obs) == 2
    assert isinstance(agent_obs[0], TextContent)
    assert agent_obs[0].text == BrowserObservation.ERROR_MESSAGE_HEADER
    assert isinstance(agent_obs[1], TextContent)
    assert "Test error" in agent_obs[1].text


def test_browser_observation_output_truncation():
    """Test output truncation for very long outputs."""
    # Create a very long output string
    long_output = "x" * 100000  # 100k characters
    observation = BrowserObservation.from_text(text=long_output)

    agent_obs = observation.to_llm_content

    # Should be truncated to MAX_BROWSER_OUTPUT_SIZE (50000)
    assert len(agent_obs) == 1
    assert isinstance(agent_obs[0], TextContent)
    assert len(agent_obs[0].text) <= 50000
    assert "<response clipped>" in agent_obs[0].text


def test_browser_observation_screenshot_data_url_conversion():
    """Test that screenshot data is properly converted to data URL."""
    screenshot_data = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChAI9jU77zgAAAABJRU5ErkJggg=="  # noqa: E501
    observation = BrowserObservation.from_text(
        text="Test", screenshot_data=screenshot_data
    )

    agent_obs = observation.to_llm_content
    expected_data_url = f"data:image/png;base64,{screenshot_data}"

    assert len(agent_obs) == 2
    assert isinstance(agent_obs[1], ImageContent)
    assert agent_obs[1].image_urls[0] == expected_data_url


def test_browser_observation_empty_screenshot_handling():
    """Test handling of empty or None screenshot data."""
    observation = BrowserObservation.from_text(text="Test", screenshot_data="")
    agent_obs = observation.to_llm_content
    assert len(agent_obs) == 1  # Only text content, no image

    observation = BrowserObservation.from_text(text="Test", screenshot_data=None)
    agent_obs = observation.to_llm_content
    assert len(agent_obs) == 1  # Only text content, no image


def test_browser_observation_mime_type_detection():
    """Test MIME type detection for different image formats."""
    test_cases = [
        (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",  # noqa: E501
            "image/png",
        ),
        (
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQH/2wBDAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQH/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAv/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwA/",  # noqa: E501
            "image/jpeg",
        ),
        (
            "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7",
            "image/gif",
        ),
        (
            "UklGRiQAAABXRUJQVlA4IBgAAAAwAQCdASoBAAEAAQAcJaQAA3AA/v3AgAA=",
            "image/webp",
        ),
        (
            "AAAABBBBCCCC",  # Unknown format
            "image/png",  # Falls back to PNG
        ),
    ]

    for screenshot_data, expected_mime_type in test_cases:
        observation = BrowserObservation.from_text(
            text="Test", screenshot_data=screenshot_data
        )
        agent_obs = observation.to_llm_content

        assert len(agent_obs) == 2
        assert isinstance(agent_obs[1], ImageContent)
        assert (
            agent_obs[1].image_urls[0].startswith(f"data:{expected_mime_type};base64,")
        )


def test_secret_masker_leaves_screenshot_and_image_urls_alone():
    """Registered secrets that collide with base64 must not corrupt the blob.

    Regression for issue #5224: ``SecretRegistry.mask_secrets_in_model`` ran a
    substring ``.replace`` across every string field and spliced
    ``<secret-hidden>`` into the middle of a screenshot payload, wedging the
    conversation. Both ``BrowserObservation.screenshot_data`` and the
    ``data:...;base64,...`` URLs that carry it into ``ImageContent`` must
    round-trip untouched.
    """
    registry = SecretRegistry()
    # 6-char base64-safe secret guaranteed to collide with realistic payloads.
    registry.update_secrets({"MY_KEY": "XYZ789"})
    registry.get_secret_value("MY_KEY")

    payload = f"iVBORw0KGgoAAA{'XYZ789'}SUhEUgAAB4A=="
    observation = BrowserObservation.from_text(
        text=f"leaked {'XYZ789'} in prose", screenshot_data=payload
    )

    masked = registry.mask_secrets_in_model(observation)

    # Opaque blob round-trips byte-for-byte, ordinary text is still masked.
    assert masked.screenshot_data == payload
    assert "XYZ789" not in masked.text

    # And the data-URL rendered from that blob is not mutated either.
    image = next(
        item for item in masked.to_llm_content if isinstance(item, ImageContent)
    )
    masked_image = registry.mask_secrets_in_model(image)
    assert masked_image.image_urls[0].endswith(payload)
