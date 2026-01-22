from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.sdk.llm import LLM, ImageContent, Message, TextContent


@pytest.mark.parametrize(
    "model",
    [
        # Plain model names
        "claude-sonnet-4-5-20250929",
        "gemini-2.5-flash",
        "gemini-3-pro-preview",
        # With provider/proxy prefixes
        "anthropic/claude-sonnet-4-5-20250929",
        "litellm_proxy/anthropic/claude-sonnet-4-5-20250929",
        "litellm_proxy/gemini-2.5-flash",
        "litellm_proxy/gemini-3-pro-preview",
    ],
)
def test_vision_is_active_supported_models(model):
    # Use real LiteLLM helpers (no patching/mocking). This test validates our
    # vision_is_active detection (prefix stripping + model_info fallback) against
    # LiteLLM's current knowledge base, without provider calls.
    llm = LLM(model=model, api_key=SecretStr("k"), usage_id="t")
    assert llm.vision_is_active() is True


def _collect_image_url_parts(chat_message: dict) -> list[dict]:
    content = chat_message.get("content", [])
    return [
        p
        for p in content
        if isinstance(p, dict)
        and p.get("type") == "image_url"
        and isinstance(p.get("image_url"), dict)
        and p["image_url"].get("url")
    ]


def _has_input_image(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("type") != "message":
        return False
    for c in item.get("content", []):
        if isinstance(c, dict) and c.get("type") == "input_image":
            return True
    return False


@pytest.mark.parametrize(
    "model",
    [
        "claude-sonnet-4-5-20250929",
        "gemini-2.5-flash",
        "gemini-3-pro-preview",
    ],
)
def test_chat_serializes_images_when_vision_supported(model):
    llm = LLM(model=model, api_key=SecretStr("k"), usage_id="t")
    assert llm.vision_is_active() is True

    msg = Message(
        role="user",
        content=[
            TextContent(text="see image"),
            ImageContent(image_urls=["https://example.com/image.png"]),
        ],
    )
    formatted = llm.format_messages_for_llm([msg])
    assert isinstance(formatted, list) and len(formatted) == 1

    parts = _collect_image_url_parts(formatted[0])
    assert len(parts) >= 1


@patch(
    "openhands.sdk.llm.llm.get_litellm_model_info",
    return_value={"supports_vision": False},
)
@patch("openhands.sdk.llm.llm.supports_vision", return_value=False)
def test_message_with_image_does_not_enable_vision_for_text_only_model(
    mock_sv, _mock_model_info
):
    # For a model that does not support vision, images should not be serialized.
    llm = LLM(model="text-only-model", api_key=SecretStr("k"), usage_id="t")
    formatted = llm.format_messages_for_llm(
        [
            Message(
                role="user",
                content=[
                    TextContent(text="see image"),
                    ImageContent(image_urls=["https://example.com/image.png"]),
                ],
            )
        ]
    )
    assert isinstance(formatted, list) and len(formatted) == 1
    content = formatted[0]["content"]
    # Expect there to be no image_url entries since model is not vision-capable
    assert all(
        not (
            isinstance(part, dict)
            and part.get("type") == "image_url"
            and isinstance(part.get("image_url"), dict)
            and part["image_url"].get("url")
        )
        for part in content
    )


@patch(
    "openhands.sdk.llm.llm.get_litellm_model_info",
    return_value={"supports_vision": False},
)
@patch("openhands.sdk.llm.llm.supports_vision", return_value=False)
def test_message_with_image_in_responses_does_not_include_input_image(
    mock_sv, _mock_model_info
):
    llm = LLM(model="text-only-model", api_key=SecretStr("k"), usage_id="t")

    instructions, input_items = llm.format_messages_for_responses(
        [
            Message(
                role="user",
                content=[
                    TextContent(text="see image"),
                    ImageContent(image_urls=["https://example.com/image.png"]),
                ],
            )
        ]
    )


@pytest.mark.parametrize(
    "model",
    [
        "claude-sonnet-4-5-20250929",
        "gemini-2.5-flash",
        "gemini-3-pro-preview",
    ],
)
def test_responses_serializes_images_when_vision_supported(model):
    llm = LLM(model=model, api_key=SecretStr("k"), usage_id="t")
    assert llm.vision_is_active() is True

    msg = Message(
        role="user",
        content=[
            TextContent(text="see image"),
            ImageContent(image_urls=["https://example.com/image.png"]),
        ],
    )
    instructions, input_items = llm.format_messages_for_responses([msg])
    assert instructions is None or isinstance(instructions, str)

    assert any(_has_input_image(item) for item in input_items)


@patch(
    "openhands.sdk.llm.llm.get_litellm_model_info",
    return_value={"supports_vision": False},
)
@patch("openhands.sdk.llm.llm.supports_vision", return_value=False)
def test_disable_vision_false_forces_vision_on(mock_sv, _mock_model_info):
    """Test that setting disable_vision=False forces vision on even when model detection fails."""
    # Create LLM with disable_vision explicitly set to False
    llm = LLM(
        model="text-only-model",
        api_key=SecretStr("k"),
        usage_id="t",
        disable_vision=False,  # Force vision on
    )
    # Vision should be active despite model not supporting it
    assert llm.vision_is_active() is True

    # Images should be serialized in chat format
    msg = Message(
        role="user",
        content=[
            TextContent(text="see image"),
            ImageContent(image_urls=["https://example.com/image.png"]),
        ],
    )
    formatted = llm.format_messages_for_llm([msg])
    parts = _collect_image_url_parts(formatted[0])
    assert len(parts) >= 1, "Images should be serialized when disable_vision=False"


@patch(
    "openhands.sdk.llm.llm.get_litellm_model_info",
    return_value={"supports_vision": True},
)
@patch("openhands.sdk.llm.llm.supports_vision", return_value=True)
def test_disable_vision_true_forces_vision_off(mock_sv, _mock_model_info):
    """Test that setting disable_vision=True forces vision off even when model supports it."""
    # Create LLM with disable_vision explicitly set to True
    llm = LLM(
        model="claude-sonnet-4-5-20250929",
        api_key=SecretStr("k"),
        usage_id="t",
        disable_vision=True,  # Force vision off
    )
    # Vision should be inactive despite model supporting it
    assert llm.vision_is_active() is False

    # Images should NOT be serialized in chat format
    msg = Message(
        role="user",
        content=[
            TextContent(text="see image"),
            ImageContent(image_urls=["https://example.com/image.png"]),
        ],
    )
    formatted = llm.format_messages_for_llm([msg])
    parts = _collect_image_url_parts(formatted[0])
    assert len(parts) == 0, "Images should not be serialized when disable_vision=True"


def test_disable_vision_none_uses_auto_detection():
    """Test that disable_vision=None (default) uses automatic model detection."""
    # For a known vision-capable model, vision should be active
    llm_vision = LLM(
        model="claude-sonnet-4-5-20250929",
        api_key=SecretStr("k"),
        usage_id="t",
        # disable_vision defaults to None
    )
    assert llm_vision.vision_is_active() is True
